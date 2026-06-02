"""Extended macro series backfill via FRED + BOK ECOS.

Adds to macro_series the canonical macro indicators the regime
classifier + LightGBM trainer can use beyond the basic FDR series:

US (FRED):
- RATE_US_2Y, RATE_US_3M  (yield curve)
- CPI_US, M2_US, UNRATE_US, INDPRO_US, PAYEMS_US, RETAILSALES_US

KR (BOK ECOS):
- RATE_KR_BASE   (기준금리)
- CPI_KR, M2_KR

Uses internal series_code conventions consistent with regime.runner
expectations and the rest of the codebase.

Usage:

    uv run python scripts/macro_extended_backfill.py [--start 2024-01-01]
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

# Internal code -> FRED series id
FRED_SERIES: dict[str, str] = {
    "RATE_US_10Y": "DGS10",          # 10-Year Treasury constant maturity
    "RATE_US_2Y": "DGS2",
    "RATE_US_3M": "DGS3MO",
    "CPI_US": "CPIAUCSL",            # CPI All Urban Consumers (monthly, SA)
    "M2_US": "M2SL",
    "UNRATE_US": "UNRATE",           # Unemployment rate (monthly)
    "INDPRO_US": "INDPRO",           # Industrial Production Index
    "PAYEMS_US": "PAYEMS",           # Non-farm Payrolls
    "RETAILSALES_US": "RSAFS",       # Retail sales SA
    "FEDFUNDS_US": "DFF",            # Federal funds rate (daily)
}

# Internal code -> BOK ECOS (stat_code, item_code, freq)
# Stat codes per ECOS docs:
#  722Y001 — 한국은행 기준금리 (daily)
#  901Y009 — 소비자물가지수 (monthly)
#  101Y004 — 통화량 M2 (monthly)
BOK_SERIES: dict[str, tuple[str, str, str]] = {
    "RATE_KR_BASE": ("722Y001", "0101000", "D"),
    "CPI_KR": ("901Y009", "0", "M"),
    "M2_KR": ("101Y004", "BBHA00", "M"),
}


def _fred_throttle() -> None:
    time.sleep(0.11)  # FRED 120 req/min limit, padded


def fetch_fred(series_id: str, start: str, end: str, api_key: str) -> list[dict]:
    r = httpx.get(FRED_BASE, params={
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
        "observation_end": end,
    }, timeout=30)
    r.raise_for_status()
    return r.json().get("observations", [])


def _parse_fred_value(v: str) -> float | None:
    if v in ("", ".", "N/A"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def fetch_bok_ecos(stat: str, item: str, freq: str, start: str, end: str, api_key: str) -> list[dict]:
    # ECOS format: /{key}/json/{lang}/{start}/{end}/{stat}/{freq}/{from}/{to}/{item1}/...
    # freq=D needs YYYYMMDD; M needs YYYYMM; A needs YYYY
    def _f(d: str) -> str:
        s = d.replace("-", "")
        if freq == "M":
            return s[:6]
        if freq == "A":
            return s[:4]
        return s
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}"
           f"/json/kr/1/10000/{stat}/{freq}/{_f(start)}/{_f(end)}/{item}")
    r = httpx.get(url, timeout=60)
    r.raise_for_status()
    return r.json().get("StatisticSearch", {}).get("row", []) or []


def _bok_to_date(time_str: str, freq: str) -> date | None:
    if not time_str:
        return None
    try:
        if freq == "D":
            return datetime.strptime(time_str, "%Y%m%d").date()
        if freq == "M":
            return datetime.strptime(time_str + "01", "%Y%m%d").date()
        if freq == "A":
            return datetime.strptime(time_str + "0101", "%Y%m%d").date()
    except ValueError:
        return None
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=(date.today() - timedelta(days=730)).isoformat())
    ap.add_argument("--end", default=date.today().isoformat())
    args = ap.parse_args()

    settings = get_settings()
    fred_key = settings.fred_api_key.get_secret_value() if settings.fred_api_key else ""
    bok_key = settings.bok_ecos_api_key.get_secret_value() if settings.bok_ecos_api_key else ""

    now = datetime.now(timezone.utc)
    print(f"Macro extended backfill :: {args.start} -> {args.end}")
    summary: list[tuple[str, int, str]] = []

    if fred_key:
        for code, sid in FRED_SERIES.items():
            try:
                obs = fetch_fred(sid, args.start, args.end, fred_key)
            except Exception as e:
                print(f"  {code} ({sid}): FAIL {type(e).__name__}: {e}")
                summary.append((code, 0, "FAIL"))
                continue
            rows = []
            for o in obs:
                ts = datetime.strptime(o["date"], "%Y-%m-%d").date()
                v = _parse_fred_value(o.get("value", ""))
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
            print(f"  {code} ({sid}): {len(rows)} rows", flush=True)
            summary.append((code, len(rows), "ok"))
            _fred_throttle()
    else:
        print("  FRED key missing — skipping US macro")

    if bok_key:
        for code, (stat, item, freq) in BOK_SERIES.items():
            try:
                obs = fetch_bok_ecos(stat, item, freq, args.start, args.end, bok_key)
            except Exception as e:
                print(f"  {code} ({stat}): FAIL {type(e).__name__}: {e}")
                summary.append((code, 0, "FAIL"))
                continue
            rows = []
            for o in obs:
                ts = _bok_to_date(o.get("TIME", ""), freq)
                if ts is None:
                    continue
                try:
                    v = float(o.get("DATA_VALUE", ""))
                except (TypeError, ValueError):
                    continue
                rows.append({
                    "series_code": code, "ts": ts, "value": v,
                    "source": "bok_ecos", "as_of_ts": now,
                })
            if rows:
                with session_scope() as s:
                    stmt = pg_insert(MacroSeries).values(rows).on_conflict_do_nothing(
                        index_elements=["ts", "series_code"]
                    )
                    s.execute(stmt)
            print(f"  {code} ({stat}): {len(rows)} rows", flush=True)
            summary.append((code, len(rows), "ok"))
            time.sleep(0.5)
    else:
        print("  BOK ECOS key missing — skipping KR macro")

    print("\nSummary:")
    for code, n, status in summary:
        print(f"  {code:20s} rows={n:>6d}  {status}")


if __name__ == "__main__":
    main()
