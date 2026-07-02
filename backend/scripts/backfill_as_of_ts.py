"""Backfill daily_prices.as_of_ts to the bar's REAL availability (market close).

Historical bars were bulk-loaded with as_of_ts = the load timestamp (~2026-06),
so the point-in-time guard in `technical.runner._load_bars` (`as_of_ts <= as_of`)
rejects ALL bars for any historical as_of → `score_one_ticker` returns None off
the production path (backtests use a trade_date workaround). This recomputes
as_of_ts = trade_date's session close (UTC) + a small lag, per market — matching
what live ingestion writes — so historical PIT scoring works.

Safe & idempotent: DRY-RUN by default (reports how many rows would change);
pass --apply to execute. One UPDATE per (market, trade_date).

Usage:
    uv run python scripts/backfill_as_of_ts.py                 # dry-run, both markets
    uv run python scripts/backfill_as_of_ts.py --market US --apply
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from core.db import session_scope
from data.price.kr_prices import _as_of_for_kr
from data.price.us_prices import _as_of_for_us

LAG = timedelta(minutes=2)   # matches the small ingestion lag
_ASOF = {"KR": _as_of_for_kr, "US": _as_of_for_us}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["KR", "US"], default=None, help="default: both")
    ap.add_argument("--apply", action="store_true", help="execute (default: dry-run)")
    args = ap.parse_args()
    markets = [args.market] if args.market else ["KR", "US"]

    for market in markets:
        asof_fn = _ASOF[market]
        with session_scope() as s:
            dates = [r[0] for r in s.execute(text(
                "SELECT DISTINCT trade_date FROM daily_prices WHERE market=:m ORDER BY trade_date"
            ), {"m": market}).all()]
        print(f"[{market}] {len(dates)} distinct trade dates", flush=True)
        changed = 0
        for d in dates:
            correct = asof_fn(d, LAG)
            with session_scope() as s:
                if args.apply:
                    res = s.execute(text(
                        "UPDATE daily_prices SET as_of_ts=:ts "
                        "WHERE market=:m AND trade_date=:d AND (as_of_ts IS DISTINCT FROM :ts)"
                    ), {"ts": correct, "m": market, "d": d})
                    changed += res.rowcount or 0
                else:
                    n = s.execute(text(
                        "SELECT count(*) FROM daily_prices "
                        "WHERE market=:m AND trade_date=:d AND (as_of_ts IS DISTINCT FROM :ts)"
                    ), {"ts": correct, "m": market, "d": d}).scalar()
                    changed += int(n or 0)
        verb = "updated" if args.apply else "would update"
        print(f"[{market}] {verb} {changed:,} rows (as_of_ts → session-close UTC)", flush=True)
    if not args.apply:
        print("\nDRY-RUN — re-run with --apply to execute.", flush=True)


if __name__ == "__main__":
    main()
