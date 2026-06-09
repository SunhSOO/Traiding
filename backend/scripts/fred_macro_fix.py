"""Re-backfill short macro series with proper FRED codes.

Fixes the macro_series codes that were missing or only partial:
  - CPI_US (CPIAUCSL)
  - CPI_KR (KORCPIALLMINMEI - World Bank via FRED)
  - FEDFUNDS_US (FEDFUNDS)
  - EXIST_HOME_SALES_US (EXHOSLUSM495S)
  - GDP_US (GDP / GDPC1 real)
  - UNRATE_US (UNRATE)
  - M2_US (M2SL)
  - PPI_US (PPIFIS — already in extra but ensure 10y)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.postgresql import insert as pg_insert
from core.config import get_settings
from core.db import session_scope
from core.models.prices import MacroSeries


FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "CPI_US": "CPIAUCSL",
    "CPI_KR": "KORCPIALLMINMEI",
    "FEDFUNDS_US": "FEDFUNDS",
    "EXIST_HOME_SALES_US": "EXHOSLUSM495S",
    "GDP_US": "GDPC1",          # Real GDP quarterly
    "UNRATE_US": "UNRATE",
    "M2_US": "M2SL",
    "PPI_US_FIX": "PPIFIS",
    "INDPRO_US": "INDPRO",      # Industrial Production
    "REAL_PCE_US": "PCEC96",    # Real Personal Consumption Expenditures
    "TWEXBGSMTH": "TWEXBGSMTH", # Trade-Weighted Dollar
    "VIX_FIX": "VIXCLS",
    "T10Y2Y": "T10Y2Y",         # Yield curve 10Y-2Y
}


def fetch_fred(series_id: str, start: str, end: str, api_key: str) -> list[dict]:
    r = httpx.get(FRED_BASE, params={
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "observation_start": start, "observation_end": end,
    }, timeout=30)
    r.raise_for_status()
    return r.json().get("observations", [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    args = ap.parse_args()

    settings = get_settings()
    fred_key = settings.fred_api_key.get_secret_value() if settings.fred_api_key else ""
    if not fred_key:
        print("FRED API key missing"); sys.exit(1)

    now = datetime.now(timezone.utc)
    print(f"FRED macro fix :: {args.start} -> {args.end}")
    for code, sid in SERIES.items():
        try:
            obs = fetch_fred(sid, args.start, args.end, fred_key)
        except Exception as e:
            print(f"  {code} ({sid}): FAIL {type(e).__name__}: {e}")
            continue
        rows = []
        for o in obs:
            try:
                ts = datetime.strptime(o["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                continue
            v_raw = o.get("value", "")
            if v_raw in ("", ".", "N/A"):
                continue
            try:
                v = float(v_raw)
            except ValueError:
                continue
            rows.append({
                "series_code": code, "ts": ts, "value": v,
                "source": "fred", "as_of_ts": now,
            })
        if rows:
            with session_scope() as s:
                stmt = pg_insert(MacroSeries).values(rows).on_conflict_do_update(
                    index_elements=["ts", "series_code"],
                    set_={"value": pg_insert(MacroSeries).excluded.value,
                           "as_of_ts": pg_insert(MacroSeries).excluded.as_of_ts},
                )
                s.execute(stmt)
        print(f"  {code:25s} ({sid:20s}): {len(rows)} rows")
        time.sleep(0.15)


if __name__ == "__main__":
    main()
