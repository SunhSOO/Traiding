"""Historical Technical-score backfill.

Re-runs `technical.score_market` once per trading day in the trailing
window, so `module_scores` accumulates a real time series for Phase 3
training (which pairs scores at T with forward returns).

Prerequisite: `daily_prices.as_of_ts` must reflect each row's true
become-known time (close + ~16h). The technical loader filters bars
on `as_of_ts <= as_of`, so a bulk-backfilled `as_of_ts = now()`
prevents historical scoring.

Usage:

    uv run python scripts/backfill_technical_history.py --days 60
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import distinct, select

from core.db import session_scope
from core.models.prices import DailyPrice
from core.types import Market
from technical.runner import score_market


def trading_dates_back(session, market: str, days: int) -> list[date]:
    """Return up to ``days`` distinct trade_dates for the market, newest first."""
    today = date.today()
    rows = list(session.execute(
        select(distinct(DailyPrice.trade_date))
        .where(DailyPrice.market == market, DailyPrice.trade_date < today)
        .order_by(DailyPrice.trade_date.desc())
        .limit(days)
    ))
    return [r[0] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()

    for market in (Market.KR, Market.US):
        with session_scope() as s:
            dates = trading_dates_back(s, market.value, args.days)
        print(f"[{market.value}] backfilling Technical for {len(dates)} trading dates")
        ok, skipped, failed = 0, 0, 0
        for i, d in enumerate(dates, start=1):
            # Anchor as_of at the day's close in UTC (after market close;
            # the look-ahead guard inside the runner uses as_of_ts <= as_of,
            # and our prices have as_of_ts = trade_date + 16h UTC).
            as_of = datetime.combine(d, time(23, 0), tzinfo=timezone.utc)
            with session_scope() as s:
                report = score_market(s, market=market, as_of=as_of)
            ok += report.tickers_processed
            skipped += report.tickers_abstained
            failed += report.tickers_failed
            if i % 10 == 0 or i == len(dates):
                print(f"  [{i:3d}/{len(dates)}] last={d} "
                      f"processed={ok} abstained={skipped} failed={failed}", flush=True)
        print(f"  [{market.value}] DONE processed={ok} abstained={skipped} failed={failed}")


if __name__ == "__main__":
    main()
