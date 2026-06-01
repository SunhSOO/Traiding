"""Historical Fundamental-score backfill.

Mirrors backfill_technical_history.py but for the Fundamental module.

The fundamental scorer pulls each ticker's most-recent financial_facts
as of the supplied ``as_of`` and computes a sector-relative score.
Since financials change only at quarter ends, the score evolves slowly
— but populating it at historical timestamps is what enables the
training/rescoring loops to combine F with T+I in their freshness
window.

Usage:

    uv run python scripts/backfill_fundamental_history.py --days 90
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
from fundamental.runner import score_market


def trading_dates_back(session, market: str, days: int) -> list[date]:
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
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    for market in (Market.KR, Market.US):
        with session_scope() as s:
            dates = trading_dates_back(s, market.value, args.days)
        print(f"[{market.value}] backfilling Fundamental for {len(dates)} trading dates")
        ok, skipped = 0, 0
        for i, d in enumerate(dates, start=1):
            as_of = datetime.combine(d, time(23, 0), tzinfo=timezone.utc)
            with session_scope() as s:
                report = score_market(s, market=market, as_of=as_of)
            ok += report.tickers_processed
            skipped += report.tickers_skipped_no_data
            if i % 10 == 0 or i == len(dates):
                print(f"  [{i:3d}/{len(dates)}] last={d} "
                      f"processed={ok} skipped={skipped}", flush=True)
        print(f"  [{market.value}] DONE processed={ok} skipped={skipped}")


if __name__ == "__main__":
    main()
