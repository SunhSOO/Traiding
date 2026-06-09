"""Incremental ticker-batch feature matrix cache — Wave 3.

Processes tickers in batches of N (default 30), saves each batch to a
shard parquet, then concatenates at end. Avoids OOM on full-universe builds.

Usage:
    uv run python scripts/cache_feature_matrix_v2.py --market US --days 1820 --batch 30
"""
from __future__ import annotations

import argparse
import gc
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from core.db import session_scope
from core.models.universe import Security
from core.models.training import TickerClusterAssignment as TickerCluster
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel
from training.features_cross_section import apply_cross_section_features


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--days", type=int, default=1820, help="5y default")
    ap.add_argument("--batch", type=int, default=30, help="tickers per shard")
    ap.add_argument("--out-dir", default="var/cache")
    ap.add_argument("--start-batch", type=int, default=0, help="resume from batch N")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    shard_dir = out_dir / f"shards_{args.market}_{start}_{end}"
    shard_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"features_{args.market}_{start}_{end}.parquet"

    # Get active tickers
    with session_scope() as s:
        rows = list(s.execute(select(Security.ticker).where(
            Security.market == args.market, Security.is_active == True
        ).order_by(Security.ticker)).all())
    tickers = [r[0] for r in rows]
    print(f"[cache-v2] {args.market} {start}->{end}, {len(tickers)} tickers, batch={args.batch}")

    total_batches = (len(tickers) + args.batch - 1) // args.batch
    for i in range(args.start_batch, total_batches):
        chunk = tickers[i * args.batch: (i + 1) * args.batch]
        shard_path = shard_dir / f"shard_{i:03d}.parquet"
        if shard_path.exists():
            print(f"  shard {i}/{total_batches}: skip (exists)")
            continue
        t0 = time.time()
        try:
            with session_scope() as s:
                df, rep = build_feature_matrix(s, market=args.market,
                                                  start=start, end=end, tickers=chunk)
                if df.empty:
                    print(f"  shard {i}/{total_batches}: empty"); continue
                close = load_close_panel(s, market=args.market,
                                           start=start - timedelta(days=10), end=end)
                df = attach_labels(df, close)
            df.to_parquet(shard_path, index=False)
            elapsed = time.time() - t0
            print(f"  shard {i:3d}/{total_batches} ({chunk[0]:>6}..{chunk[-1]:<6}): "
                  f"rows={rep.rows_emitted:>5d} ({elapsed:.0f}s)", flush=True)
            del df
            gc.collect()
        except Exception as e:
            print(f"  shard {i}/{total_batches} FAIL: {type(e).__name__}: {e}", flush=True)

    # Concatenate
    print(f"\n[cache-v2] concatenating shards from {shard_dir}")
    shards = sorted(shard_dir.glob("shard_*.parquet"))
    if not shards:
        print("[cache-v2] no shards to concat"); sys.exit(1)
    print(f"  {len(shards)} shards")
    dfs = []
    for s_path in shards:
        try:
            d = pd.read_parquet(s_path)
            dfs.append(d)
        except Exception as e:
            print(f"  failed to read {s_path}: {e}")
    if not dfs:
        sys.exit(1)
    full = pd.concat(dfs, ignore_index=True)
    print(f"  concatenated: rows={len(full):,} cols={len(full.columns)}")
    del dfs; gc.collect()

    # Attach cluster_id
    with session_scope() as s:
        cluster_rows = list(s.execute(
            select(TickerCluster.market, TickerCluster.ticker, TickerCluster.cluster_id)
        ).all())
    cluster_df = pd.DataFrame(cluster_rows, columns=["market", "ticker", "cluster_id"])
    full = full.merge(cluster_df, on=["market", "ticker"], how="left")

    # Apply cross-section transformations
    print("  applying cross-section...")
    full = apply_cross_section_features(full)

    full.to_parquet(final_path, index=False)
    size_mb = final_path.stat().st_size / 1024 / 1024
    print(f"[cache-v2] saved {final_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
