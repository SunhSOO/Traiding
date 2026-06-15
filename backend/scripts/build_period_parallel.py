"""Parallel feature-matrix build for an arbitrary window — uses all cores.

The single-process build is ticker-sequential (one core), so a 6-year build
took hours while 22 of 24 cores sat idle. This splits tickers across worker
processes (each `build_feature_matrix` on its own chunk + DB session), then
concatenates and applies labels + cross-section once. Produces the same
`var/_bt_period_{m}_{start}_{end}.parquet` the backtest reads (and a raw
cache), so backtest_capital --build-start/--end skips rebuilding.

Usage:
    uv run python scripts/build_period_parallel.py --market US \
        --start 2018-01-01 --end 2024-01-01 --workers 16
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import select

from core.db import session_scope, reset_engines
from core.models.universe import Security


def _init_worker() -> None:
    try:
        reset_engines()
    except Exception:
        pass


def _build_chunk(args_tuple) -> pd.DataFrame:
    market, start_iso, end_iso, tickers = args_tuple
    from training.features import build_feature_matrix
    s0, e0 = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    with session_scope() as s:
        df, _ = build_feature_matrix(s, market=market, start=s0, end=e0, tickers=tickers)
    return df


def _chunks(lst, n):
    k, m = divmod(len(lst), n)
    out, i = [], 0
    for x in range(n):
        sz = k + (1 if x < m else 0)
        if sz:
            out.append(lst[i:i+sz]); i += sz
    return out


def main() -> None:
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 8) - 2))
    ap.add_argument("--tasks-per-worker", type=int, default=3)
    args = ap.parse_args()

    period = Path(f"var/_bt_period_{args.market}_{args.start}_{args.end}.parquet")
    if period.exists():
        print(f"[pbuild] {period} already exists; nothing to do"); return

    with session_scope() as s:
        tickers = [r[0] for r in s.execute(
            select(Security.ticker).where(
                Security.market == args.market, Security.is_active.is_(True))
        )]
    t0 = time.time()
    print(f"[pbuild] {args.market} {args.start}..{args.end}  {len(tickers)} tickers  "
          f"workers={args.workers}", flush=True)

    raw = Path(f"var/_bt_raw_{args.market}_{args.start}_{args.end}.parquet")
    if raw.exists():
        print(f"[pbuild] loading raw {raw}", flush=True)
        full = pd.read_parquet(raw)
    else:
        chunks = _chunks(tickers, max(1, args.workers * args.tasks_per_worker))
        tasks = [(args.market, args.start, args.end, c) for c in chunks]
        parts, done = [], 0
        with Pool(args.workers, initializer=_init_worker) as pool:
            for df in pool.imap_unordered(_build_chunk, tasks):
                done += 1
                if df is not None and len(df):
                    parts.append(df)
                print(f"  [{done}/{len(tasks)}] chunk rows={0 if df is None else len(df)} "
                      f"| {time.time()-t0:.0f}s", flush=True)
        full = pd.concat(parts, ignore_index=True)
        full.to_parquet(raw)
        print(f"[pbuild] raw build rows={len(full):,} -> {raw} ({time.time()-t0:.0f}s)", flush=True)

    # Labels + cross-section once on the full panel (need all tickers/dates).
    from training.labels_multi import attach_labels, load_close_panel
    from training.features_cross_section import apply_cross_section_features
    s0 = date.fromisoformat(args.start); e0 = date.fromisoformat(args.end)
    with session_scope() as s:
        close = load_close_panel(s, market=args.market, start=s0 - timedelta(days=10), end=e0)
    full = attach_labels(full, close)
    full = apply_cross_section_features(full)
    full.to_parquet(period)
    print(f"[pbuild] DONE rows={len(full):,} cols={full.shape[1]} -> {period} "
          f"({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
