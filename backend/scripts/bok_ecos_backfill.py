"""Bank of Korea ECOS macro backfill — Wave 3.

ECOS API (https://ecos.bok.or.kr/api) provides KR-specific macro time series:
  - KR base rate, M2, CPI, GDP, unemployment
  - KR FX rates (USD/KRW, JPY/KRW)
  - KR sentiment indices (CSI, BSI)
  - KR housing prices, trade balance

Free with API key (issued instantly at ecos.bok.or.kr).

Schema: writes to existing macro_series table (series_code prefixed 'KR_BOK_').

Usage (uses BOK_API_KEY env var or settings.bok_api_key):
    uv run python scripts/bok_ecos_backfill.py --start 2016-01-01
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import get_settings
from core.db import session_scope
from core.models.prices import MacroSeries


BOK_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

# series_code -> (stat_code, item_code, freq) per ECOS docs
ECOS_SERIES = {
    "KR_BOK_BASE_RATE": ("722Y001", "0101000", "M"),     # 한국 기준금리 월
    "KR_BOK_M2": ("101Y003", "BBHA00", "M"),             # M2 통화
    "KR_BOK_CPI": ("901Y009", "0", "M"),                 # 소비자물가 헤드라인
    "KR_BOK_CPI_CORE": ("901Y009", "QA", "M"),           # 근원물가
    "KR_BOK_PPI": ("404Y014", "*AA", "M"),               # 생산자물가
    "KR_BOK_UNEMP": ("901Y027", "I61E", "M"),            # 실업률
    "KR_BOK_EXCHANGE_USDKRW": ("731Y001", "0000001", "D"),   # USD/KRW 일별
    "KR_BOK_EXCHANGE_JPYKRW": ("731Y001", "0000002", "D"),
    "KR_BOK_EXCHANGE_CNYKRW": ("731Y001", "0000053", "D"),
    "KR_BOK_GDP": ("200Y011", "10101", "Q"),             # GDP 분기
    "KR_BOK_INDPROD": ("901Y033", "I61E", "M"),          # 산업생산
    "KR_BOK_HOUSING_PRICE": ("407Y014", "P63A", "M"),    # 아파트가격
    "KR_BOK_TRADE_BAL": ("403Y001", "100000", "M"),      # 무역수지
    "KR_BOK_KOSPI": ("802Y001", "0001000", "D"),         # KOSPI 종합
    "KR_BOK_CSI_HEAD": ("511Y002", "FME", "M"),          # 소비자심리지수
    "KR_BOK_BSI_HEAD": ("512Y014", "C0000", "M"),        # 기업경기실사지수
    # KR government bond yield curve (817Y002 시장금리 일별) — verified item
    # codes. Note the counter-intuitive numbering: 3Y=..000, 5Y=..001, 10Y=..210.
    # Named RATE_KR_* to match the RATE_US_* convention that features.py reads.
    "RATE_KR_3Y": ("817Y002", "010200000", "D"),         # 국고채(3년)
    "RATE_KR_5Y": ("817Y002", "010200001", "D"),         # 국고채(5년)
    "RATE_KR_10Y": ("817Y002", "010210000", "D"),        # 국고채(10년)
}


def fetch_ecos(stat_code: str, item_code: str, freq: str,
                 start: str, end: str, api_key: str) -> list[dict]:
    # ECOS URL format: /<KEY>/json/kr/<page_start>/<page_end>/<STAT>/<FREQ>/<START>/<END>/<ITEM>
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/"
            f"1/2000/{stat_code}/{freq}/{start}/{end}/{item_code}")
    try:
        r = httpx.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  fetch FAIL {type(e).__name__}: {e}")
        return []
    body = data.get("StatisticSearch", {})
    if "RESULT" in body and body["RESULT"].get("CODE") != "INFO-200":
        print(f"  API ERROR: {body['RESULT']}")
        return []
    rows = body.get("row", [])
    return rows


def _parse_date(ymd: str, freq: str) -> date | None:
    """ECOS date strings:
        D: 20200115
        M: 202001 → first of month
        Q: 2020Q1 → first of quarter
        Y: 2020 → Jan 1
    """
    try:
        if freq == "D" and len(ymd) == 8:
            return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
        if freq == "M" and len(ymd) == 6:
            return date(int(ymd[:4]), int(ymd[4:6]), 1)
        if freq == "Q" and "Q" in ymd:
            y, q = ymd.split("Q")
            return date(int(y), (int(q) - 1) * 3 + 1, 1)
        if freq == "Y" and len(ymd) == 4:
            return date(int(ymd), 1, 1)
    except (ValueError, IndexError):
        return None
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    args = ap.parse_args()

    settings = get_settings()
    api_key = getattr(settings, "bok_ecos_api_key", None)
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()
    if not api_key:
        print("BOK API key missing (settings.bok_ecos_api_key)"); sys.exit(1)

    now = datetime.now(timezone.utc)
    summary = []
    for code, (stat, item, freq) in ECOS_SERIES.items():
        s_str = args.start.replace("-", "")[: (8 if freq == "D" else 6 if freq == "M" else 4)]
        e_str = args.end.replace("-", "")[: (8 if freq == "D" else 6 if freq == "M" else 4)]
        if freq == "Q":
            s_str = f"{args.start[:4]}Q1"
            e_str = f"{args.end[:4]}Q4"
        rows = fetch_ecos(stat, item, freq, s_str, e_str, api_key)
        insert_rows = []
        for r in rows:
            ts = _parse_date(r.get("TIME", ""), freq)
            val_raw = r.get("DATA_VALUE", "")
            if not ts or not val_raw:
                continue
            try:
                value = float(val_raw)
            except ValueError:
                continue
            insert_rows.append({
                "series_code": code, "ts": ts, "value": value,
                "source": "bok_ecos", "as_of_ts": now,
            })
        if insert_rows:
            with session_scope() as s:
                stmt = pg_insert(MacroSeries).values(insert_rows).on_conflict_do_nothing(
                    index_elements=["ts", "series_code"]
                )
                s.execute(stmt)
        print(f"  {code:35s} rows={len(insert_rows):>5d}")
        summary.append((code, len(insert_rows)))
        time.sleep(0.5)   # ECOS rate-limit

    print("\nSummary:")
    for code, n in summary:
        print(f"  {code:35s} {n:>5d}")


if __name__ == "__main__":
    main()
