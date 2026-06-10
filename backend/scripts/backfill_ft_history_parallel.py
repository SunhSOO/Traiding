"""Parallel 10-year Fundamental + Technical module-score backfill.

Both `backfill_technical_history.py` and `backfill_fundamental_history.py`
loop trading dates and call `score_market(market, as_of)` once per date.
Each date is independent, and `score_market` is memory-light (it loads only
a per-ticker lookback window via `_load_bars` / `_load_concepts`, not a full
panel). So the safe way to go fast is to parallelize ACROSS DATES.

OOM safety (prior OOM was the pandas feature-cache pipeline, NOT scoring):
- Each worker holds only one date's per-ticker lookback at a time → tiny RSS.
- Worker count bounded (default 16 of 24 cores) so the box keeps headroom and
  Postgres connection count stays well under max_connections.
- Each worker creates its OWN SQLAlchemy engine/session (spawn-safe); we
  dispose any inherited engine in the initializer.

Storage: module_scores are numeric rows (~2-4 GB for 10y F+T), unrelated to
the ~5TB news-text constraint that defers the 10y news backfill.

Usage:
    uv run python scripts/backfill_ft_history_parallel.py --years 10 --workers 16
    uv run python scripts/backfill_ft_history_parallel.py --years 10 --modules t   # technical only
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import distinct, select

from core.db import session_scope, reset_engines  # reset_engines: dispose cached engines
from core.models.prices import DailyPrice
from core.types import Market

_MODULES = "ft"  # set per-process in initializer


def _init_worker(modules: str) -> None:
    global _MODULES
    _MODULES = modules
    # Spawn inherits nothing live, but be explicit: drop any cached engine so
    # this process opens its own pool.
    try:
        reset_engines()
    except Exception:
        pass


def _score_chunk(args: tuple) -> tuple:
    """Process one (market, [dates]) chunk. Returns (market, n_dates, t_rows, f_rows, secs)."""
    market_value, dates = args
    market = Market(market_value)
    t0 = time.time()
    t_rows = f_rows = 0
    # Import here so each worker imports in its own process (spawn-safe).
    if "t" in _MODULES:
        from technical.runner import score_market as t_score
    if "f" in _MODULES:
        from fundamental.runner import score_market as f_score
    for d in dates:
        as_of = datetime.combine(d, dtime(23, 0), tzinfo=timezone.utc)
        if "t" in _MODULES:
            with session_scope() as s:
                rep = t_score(s, market=market, as_of=as_of)
                t_rows += getattr(rep, "tickers_processed", 0)
        if "f" in _MODULES:
            with session_scope() as s:
                rep = f_score(s, market=market, as_of=as_of)
                f_rows += getattr(rep, "tickers_processed", 0)
    return (market_value, len(dates), t_rows, f_rows, time.time() - t0)


def trading_dates(market: str, since: date) -> list[date]:
    with session_scope() as s:
        rows = s.execute(
            select(distinct(DailyPrice.trade_date))
            .where(DailyPrice.market == market,
                   DailyPrice.trade_date >= since,
                   DailyPrice.trade_date < date.today())
            .order_by(DailyPrice.trade_date.desc())
        )
        return [r[0] for r in rows]


def _chunks(lst: list, n: int) -> list[list]:
    """Split lst into n roughly-equal contiguous chunks."""
    if n <= 1:
        return [lst]
    k, m = divmod(len(lst), n)
    out = []
    i = 0
    for x in range(n):
        size = k + (1 if x < m else 0)
        out.append(lst[i:i + size])
        i += size
    return [c for c in out if c]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--modules", type=str, default="ft",
                    help="'ft' (both), 't' (technical only), 'f' (fundamental only)")
    ap.add_argument("--tasks-per-worker", type=int, default=6,
                    help="chunk granularity for progress + load balance")
    ap.add_argument("--markets", type=str, default="KR,US",
                    help="comma-separated markets to backfill")
    args = ap.parse_args()
    _markets = [m.strip() for m in args.markets.split(",") if m.strip()]

    since = date.today() - timedelta(days=int(args.years * 365.25))
    t0 = time.time()
    print(f"[ft-backfill] years={args.years} since={since} workers={args.workers} "
          f"modules={args.modules}", flush=True)

    # Build task list: per market, split dates into workers*tasks_per_worker chunks.
    tasks: list[tuple] = []
    totals = {"KR": 0, "US": 0}
    for mk in _markets:
        ds = trading_dates(mk, since)
        totals[mk] = len(ds)
        n_chunks = max(1, args.workers * args.tasks_per_worker)
        for ch in _chunks(ds, n_chunks):
            tasks.append((mk, ch))
        print(f"  {mk}: {len(ds)} trading dates -> {sum(1 for t in tasks if t[0]==mk)} chunks "
              f"({ds[-1] if ds else None} .. {ds[0] if ds else None})", flush=True)

    print(f"  total chunks={len(tasks)} | dispatching to {args.workers} workers", flush=True)

    done_chunks = 0
    agg = {"KR": [0, 0, 0], "US": [0, 0, 0]}  # n_dates, t_rows, f_rows
    with Pool(args.workers, initializer=_init_worker, initargs=(args.modules,)) as pool:
        for mk, nd, tr, fr, secs in pool.imap_unordered(_score_chunk, tasks):
            done_chunks += 1
            agg[mk][0] += nd
            agg[mk][1] += tr
            agg[mk][2] += fr
            print(f"  [{done_chunks:3d}/{len(tasks)}] {mk} chunk: {nd} dates "
                  f"T={tr} F={fr} in {secs:.0f}s | "
                  f"cum KR(dates={agg['KR'][0]},T={agg['KR'][1]},F={agg['KR'][2]}) "
                  f"US(dates={agg['US'][0]},T={agg['US'][1]},F={agg['US'][2]}) "
                  f"| {time.time()-t0:.0f}s", flush=True)

    print(f"\n[ft-backfill] DONE in {time.time()-t0:.0f}s.", flush=True)
    for mk in ("KR", "US"):
        print(f"  {mk}: {totals[mk]} dates scored | T tickers-days={agg[mk][1]:,} "
              f"F tickers-days={agg[mk][2]:,}", flush=True)


if __name__ == "__main__":
    main()
