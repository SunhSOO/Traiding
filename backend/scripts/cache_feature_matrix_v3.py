"""Subprocess-per-shard cache builder — Wave 3.

Forks a subprocess for each batch of N tickers, isolating memory.
Once subprocess exits, OS reclaims memory fully. Avoids Python GC issues.

Usage:
    uv run python scripts/cache_feature_matrix_v3.py --market US --days 1820 --batch 10
"""
from __future__ import annotations

import argparse
import subprocess
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
from training.features_cross_section import apply_cross_section_features


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--days", type=int, default=1820)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--out-dir", default="var/cache")
    ap.add_argument("--start-batch", type=int, default=0)
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    shard_dir = out_dir / f"shards_{args.market}_{start}_{end}"
    shard_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"features_{args.market}_{start}_{end}.parquet"

    with session_scope() as s:
        tickers = [r[0] for r in s.execute(select(Security.ticker).where(
            Security.market == args.market, Security.is_active == True
        ).order_by(Security.ticker)).all()]
    print(f"[cache-v3] {args.market} {start}->{end}, {len(tickers)} tickers, batch={args.batch}, subprocess-isolated")

    total = (len(tickers) + args.batch - 1) // args.batch
    for i in range(args.start_batch, total):
        chunk = tickers[i * args.batch: (i + 1) * args.batch]
        shard_path = shard_dir / f"shard_{i:03d}.parquet"
        if shard_path.exists():
            print(f"  shard {i}/{total}: skip (exists)")
            continue
        t0 = time.time()
        cmd = ["uv", "run", "python", "scripts/cache_shard_worker.py",
                "--market", args.market, "--start", str(start), "--end", str(end),
                "--tickers", ",".join(chunk), "--out", str(shard_path)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                print(f"  shard {i}/{total} FAIL rc={r.returncode}: {r.stderr[-300:]}", flush=True)
                continue
            elapsed = time.time() - t0
            # extract worker print
            for line in r.stdout.splitlines():
                if "[worker]" in line:
                    print(f"  shard {i:3d}/{total} {chunk[0]:>6}..{chunk[-1]:<6} ({elapsed:.0f}s): {line}", flush=True)
        except subprocess.TimeoutExpired:
            print(f"  shard {i}/{total} TIMEOUT", flush=True)

    # Concatenate
    print(f"\n[cache-v3] concatenating from {shard_dir}")
    shards = sorted(shard_dir.glob("shard_*.parquet"))
    if not shards:
        print("[cache-v3] no shards"); sys.exit(1)
    print(f"  {len(shards)} shards")
    dfs = [pd.read_parquet(p) for p in shards]
    full = pd.concat(dfs, ignore_index=True)
    print(f"  concatenated: rows={len(full):,} cols={len(full.columns)}")
    del dfs

    with session_scope() as s:
        cluster_rows = list(s.execute(
            select(TickerCluster.market, TickerCluster.ticker, TickerCluster.cluster_id)
        ).all())
    cluster_df = pd.DataFrame(cluster_rows, columns=["market", "ticker", "cluster_id"])
    full = full.merge(cluster_df, on=["market", "ticker"], how="left")

    print("  applying cross-section...")
    full = apply_cross_section_features(full)

    full.to_parquet(final_path, index=False)
    size_mb = final_path.stat().st_size / 1024 / 1024
    print(f"[cache-v3] saved {final_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
