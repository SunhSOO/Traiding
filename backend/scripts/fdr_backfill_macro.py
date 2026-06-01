"""FDR-based macro_series backfill — no API key required.

Populates the series codes the regime classifier reads:

    * VIX, IDX_SP500_FRED, IDX_KOSPI_ECOS, FX_DXY
    * RATE_US_10Y, RATE_US_2Y  (FDR symbols: US10YT=X, US2YT=X)

Series codes intentionally match the FRED/ECOS codes the rest of the
codebase expects, so when those upstream adapters become available the
regime job switches sources transparently via the source column.

Usage:

    uv run python scripts/fdr_backfill_macro.py [--start 2024-01-01]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import FinanceDataReader as fdr
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.db import session_scope
from core.models.prices import MacroSeries


# Internal series code -> FDR symbol
SERIES_MAP: dict[str, str] = {
    "VIX": "VIX",
    "IDX_SP500_FRED": "US500",
    "IDX_KOSPI_ECOS": "KS11",
    "FX_DXY": "DX-Y.NYB",
    # Yahoo Treasury yield indices: ^TNX = 10-year, ^FVX = 5-year.
    # 2-year isn't exposed; without a FRED key the yield-curve voter
    # in regime classifier degrades to None (still produces a vote
    # from the other 4 inputs).
    "RATE_US_10Y": "^TNX",
}


def fetch_one(symbol: str, start: str, end: str):
    return fdr.DataReader(symbol, start=start, end=end)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=(date.today() - timedelta(days=730)).isoformat())
    ap.add_argument("--end", default=date.today().isoformat())
    args = ap.parse_args()

    print(f"FDR macro backfill :: {args.start} -> {args.end}")
    now = datetime.now(timezone.utc)
    summary: list[tuple[str, int, str]] = []

    for code, symbol in SERIES_MAP.items():
        try:
            df = fetch_one(symbol, args.start, args.end)
        except Exception as e:
            print(f"  {code} ({symbol}): FAIL {type(e).__name__}: {e}")
            summary.append((code, 0, f"FAIL: {type(e).__name__}"))
            continue
        if df is None or df.empty:
            print(f"  {code} ({symbol}): empty")
            summary.append((code, 0, "empty"))
            continue

        rows: list[dict] = []
        for idx, row in df.iterrows():
            val = row.get("Close", row.get("Adj Close"))
            if val is None or val != val:
                continue
            rows.append({
                "series_code": code,
                "ts": idx.date() if hasattr(idx, "date") else idx,
                "value": float(val),
                "source": "fdr",
                "as_of_ts": now,
            })
        if not rows:
            print(f"  {code} ({symbol}): no usable Close values")
            summary.append((code, 0, "no close"))
            continue

        CHUNK = 2000
        with session_scope() as s:
            for k in range(0, len(rows), CHUNK):
                chunk = rows[k:k + CHUNK]
                stmt = pg_insert(MacroSeries).values(chunk).on_conflict_do_nothing(
                    index_elements=["ts", "series_code"]
                )
                s.execute(stmt)
        print(f"  {code} ({symbol}): {len(rows)} rows")
        summary.append((code, len(rows), "ok"))

    print("\nSummary:")
    for code, n, status in summary:
        print(f"  {code:20s} rows={n:>6d}  {status}")


if __name__ == "__main__":
    main()
