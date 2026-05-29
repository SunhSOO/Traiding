"""FDR-based US daily-price backfill.

Walks every active US security in the DB and pulls daily OHLCV
from FinanceDataReader for the requested window. Inserts via
ON CONFLICT DO NOTHING so re-runs are idempotent.

Usage:

    uv run python scripts/fdr_backfill_us.py [--start 2024-01-01] [--end 2026-05-29] [--batch 25]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone

import FinanceDataReader as fdr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.db import session_scope
from core.models.prices import DailyPrice
from core.models.universe import Security


def _row_from_fdr(market: str, ticker: str, idx, row, as_of: datetime) -> dict | None:
    close = row["Close"]
    if close != close:
        return None
    open_ = row["Open"] if row["Open"] == row["Open"] else close
    high = row["High"] if row["High"] == row["High"] else close
    low = row["Low"] if row["Low"] == row["Low"] else close
    volume = row["Volume"] if row["Volume"] == row["Volume"] else 0
    adj_close_raw = row.get("Adj Close", close)
    adj_close = float(adj_close_raw) if adj_close_raw == adj_close_raw else float(close)
    return {
        "market": market,
        "ticker": ticker,
        "trade_date": idx.date() if hasattr(idx, "date") else idx,
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": int(volume),
        "adj_close": adj_close,
        "source": "fdr",
        "as_of_ts": as_of,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=(date.today() - timedelta(days=540)).isoformat())
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--batch", type=int, default=25, help="commit every N tickers")
    ap.add_argument("--limit", type=int, default=0, help="stop after N tickers (0 = all)")
    ap.add_argument("--skip-existing-min-rows", type=int, default=200,
                    help="skip tickers that already have >= this many rows in DB")
    args = ap.parse_args()

    print(f"FDR backfill US :: {args.start} -> {args.end}")
    with session_scope() as s:
        tickers = list(s.scalars(
            select(Security.ticker)
            .where(Security.market == "US", Security.is_active.is_(True))
            .order_by(Security.ticker)
        ))
    print(f"  {len(tickers)} active US tickers in DB")

    if args.skip_existing_min_rows > 0:
        with session_scope() as s:
            from sqlalchemy import func
            already = dict(s.execute(
                select(DailyPrice.ticker, func.count())
                .where(DailyPrice.market == "US")
                .group_by(DailyPrice.ticker)
            ).all())
        before = len(tickers)
        tickers = [t for t in tickers if already.get(t, 0) < args.skip_existing_min_rows]
        print(f"  skipping {before - len(tickers)} tickers already at >= "
              f"{args.skip_existing_min_rows} rows")

    if args.limit > 0:
        tickers = tickers[:args.limit]
        print(f"  limited to first {len(tickers)} tickers")

    total_ok, total_fail, total_rows = 0, 0, 0
    batch_rows: list[dict] = []
    fail_examples: list[str] = []
    as_of = datetime.now(timezone.utc)

    for i, t in enumerate(tickers, start=1):
        try:
            df = fdr.DataReader(t, start=args.start, end=args.end)
        except Exception as e:
            total_fail += 1
            if len(fail_examples) < 5:
                fail_examples.append(f"{t}: {type(e).__name__}")
            continue
        if df is None or df.empty:
            total_fail += 1
            if len(fail_examples) < 5:
                fail_examples.append(f"{t}: empty")
            continue
        for idx, row in df.iterrows():
            try:
                rec = _row_from_fdr("US", t, idx, row, as_of)
            except Exception:
                continue
            if rec is None:
                continue
            batch_rows.append(rec)
        total_ok += 1

        if i % args.batch == 0 or i == len(tickers):
            if batch_rows:
                # Postgres limit: 65535 params per query. 11 cols -> max ~5950 rows.
                CHUNK = 2000
                with session_scope() as s:
                    for k in range(0, len(batch_rows), CHUNK):
                        chunk = batch_rows[k:k + CHUNK]
                        stmt = pg_insert(DailyPrice).values(chunk).on_conflict_do_nothing(
                            index_elements=["trade_date", "market", "ticker"]
                        )
                        s.execute(stmt)
                total_rows += len(batch_rows)
                batch_rows.clear()
            print(f"  [{i:4d}/{len(tickers)}] ok={total_ok} fail={total_fail} "
                  f"rows-attempted={total_rows}", flush=True)

    print(f"\nDone. ok={total_ok} fail={total_fail} rows-attempted={total_rows}")
    if fail_examples:
        print("  first failures:", ", ".join(fail_examples))


if __name__ == "__main__":
    main()
