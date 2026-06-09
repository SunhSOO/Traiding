"""FRED 확장 매크로 백필 — Wave 1.

추가 시리즈 (FRED 무료 API key 사용):
- ICSA (Initial Claims) - 주간 leading indicator
- CCSA (Continued Claims)
- NAPM (ISM Manufacturing PMI)
- NMFCI (ISM Services - 변경된 코드)
- UMCSENT (UMich Consumer Sentiment)
- HOUST (Housing Starts)
- PERMIT (Building Permits)
- EXHOSLUSM495S (Existing Home Sales)
- CSUSHPISA (Case-Shiller Home Price)
- DEXUSEU (USD/EUR), DEXKOUS (KRW/USD), DEXJPUS (USD/JPY)
- PCE (Personal Consumption)
- PCEPI (Core PCE)
- PPIFIS (Producer Price Index)
- DGS1, DGS5, DGS7, DGS20, DGS30 (Treasury 다양한 기간)
- DFII10 (10Y TIPS)
- T10YIE (10Y Breakeven Inflation)
- BAMLH0A0HYM2 (HY Credit Spread)
- DCOILWTICO (WTI Oil)
- POILBRENTUSDM (Brent Oil)
- DHHNGSP (Natural Gas)
- GOLDAMGBD228NLBM (Gold London Fix)

Total: 25+ 추가 시리즈.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import get_settings
from core.db import session_scope
from core.models.prices import MacroSeries


FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

EXTRA_SERIES: dict[str, str] = {
    # 주간 / 월간 leading indicators
    "JOBLESS_INITIAL_US": "ICSA",
    "JOBLESS_CONTINUED_US": "CCSA",
    "ISM_MFG_US": "NAPM",       # ISM Manufacturing PMI
    "UMICH_SENT_US": "UMCSENT", # UMich Consumer Sentiment
    "HOUSING_STARTS_US": "HOUST",
    "BUILDING_PERMITS_US": "PERMIT",
    "EXIST_HOME_SALES_US": "EXHOSLUSM495S",
    "CASE_SHILLER_US": "CSUSHPISA",
    # 환율
    "FX_USDEUR": "DEXUSEU",
    "FX_USDKRW": "DEXKOUS",
    "FX_USDJPY": "DEXJPUS",
    "FX_USDCNY": "DEXCHUS",
    # 인플레이션
    "PCE_US": "PCEPILFE",        # Core PCE
    "PCE_HEAD_US": "PCEPI",
    "PPI_US": "PPIFIS",
    # 채권
    "RATE_US_1Y": "DGS1",
    "RATE_US_5Y": "DGS5",
    "RATE_US_7Y": "DGS7",
    "RATE_US_20Y": "DGS20",
    "RATE_US_30Y": "DGS30",
    "RATE_US_10Y_TIPS": "DFII10",
    "BREAKEVEN_INFLATION_10Y": "T10YIE",
    # 신용 스프레드
    "HY_CREDIT_SPREAD": "BAMLH0A0HYM2",
    # 원자재
    "WTI_OIL": "DCOILWTICO",
    "BRENT_OIL": "POILBRENTUSDM",
    "NAT_GAS": "DHHNGSP",
    "GOLD_LONDON": "GOLDAMGBD228NLBM",
    "COPPER": "PCOPPUSDM",
}


def fetch_fred(series_id: str, start: str, end: str, api_key: str) -> list[dict]:
    r = httpx.get(FRED_BASE, params={
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "observation_start": start, "observation_end": end,
    }, timeout=30)
    r.raise_for_status()
    return r.json().get("observations", [])


def _parse_value(v: str) -> float | None:
    if v in ("", ".", "N/A"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=(date.today() - timedelta(days=730)).isoformat())
    ap.add_argument("--end", default=date.today().isoformat())
    args = ap.parse_args()

    settings = get_settings()
    fred_key = settings.fred_api_key.get_secret_value() if settings.fred_api_key else ""
    if not fred_key:
        print("FRED API key missing"); sys.exit(1)

    now = datetime.now(timezone.utc)
    print(f"FRED extra backfill :: {args.start} -> {args.end} ({len(EXTRA_SERIES)} series)")
    summary = []
    for code, sid in EXTRA_SERIES.items():
        try:
            obs = fetch_fred(sid, args.start, args.end, fred_key)
        except Exception as e:
            print(f"  {code} ({sid}): FAIL {type(e).__name__}: {e}")
            summary.append((code, 0, "FAIL"))
            continue
        rows = []
        for o in obs:
            try:
                ts = datetime.strptime(o["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                continue
            v = _parse_value(o.get("value", ""))
            if v is None:
                continue
            rows.append({
                "series_code": code, "ts": ts, "value": v,
                "source": "fred", "as_of_ts": now,
            })
        if rows:
            with session_scope() as s:
                stmt = pg_insert(MacroSeries).values(rows).on_conflict_do_nothing(
                    index_elements=["ts", "series_code"]
                )
                s.execute(stmt)
        print(f"  {code} ({sid}): {len(rows)} rows")
        summary.append((code, len(rows), "ok" if rows else "empty"))
        time.sleep(0.15)   # 120/min limit, padded

    print("\nSummary:")
    for code, n, status in summary:
        print(f"  {code:30s} rows={n:>5d}  {status}")


if __name__ == "__main__":
    main()
