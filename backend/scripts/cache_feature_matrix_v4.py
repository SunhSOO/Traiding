"""Parallel subprocess cache builder — Wave 3.

v4 = v3 + concurrent.futures.ProcessPoolExecutor (4 workers default).
Each shard still isolated as subprocess; 4 shards built in parallel.

Skips already-completed shards (resume support from v3 runs).

Usage:
    uv run python scripts/cache_feature_matrix_v4.py --market US --days 1820 --batch 5 --workers 4
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from core.db import session_scope
from core.models.universe import Security
from core.models.training import TickerClusterAssignment as TickerCluster
from training.features_cross_section import apply_cross_section_features


def run_one_shard(args_tuple):
    market, start, end, tickers, shard_path = args_tuple
    if Path(shard_path).exists():
        return ("skip", shard_path, 0)
    t0 = time.time()
    cmd = ["uv", "run", "python", "scripts/cache_shard_worker.py",
            "--market", market, "--start", str(start), "--end", str(end),
            "--tickers", ",".join(tickers), "--out", shard_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        elapsed = time.time() - t0
        if r.returncode != 0:
            return ("fail", shard_path, elapsed, r.stderr[-300:])
        return ("ok", shard_path, elapsed)
    except subprocess.TimeoutExpired:
        return ("timeout", shard_path, time.time() - t0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--days", type=int, default=1820)
    ap.add_argument("--start-date", default=None, help="override start (YYYY-MM-DD)")
    ap.add_argument("--end-date", default=None, help="override end (YYYY-MM-DD)")
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out-dir", default="var/cache")
    args = ap.parse_args()

    if args.end_date:
        end = date.fromisoformat(args.end_date)
    else:
        end = date.today()
    if args.start_date:
        start = date.fromisoformat(args.start_date)
    else:
        start = end - timedelta(days=args.days)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    shard_dir = out_dir / f"shards_{args.market}_{start}_{end}"
    shard_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"features_{args.market}_{start}_{end}.parquet"

    with session_scope() as s:
        tickers = [r[0] for r in s.execute(select(Security.ticker).where(
            Security.market == args.market, Security.is_active == True
        ).order_by(Security.ticker)).all()]
    print(f"[cache-v4] {args.market} {start}->{end}, {len(tickers)} tickers, batch={args.batch}, workers={args.workers}", flush=True)

    total = (len(tickers) + args.batch - 1) // args.batch
    jobs = []
    for i in range(total):
        chunk = tickers[i * args.batch: (i + 1) * args.batch]
        shard_path = str(shard_dir / f"shard_{i:03d}.parquet")
        jobs.append((args.market, start, end, chunk, shard_path))

    # Count already-done
    done = sum(1 for j in jobs if Path(j[4]).exists())
    print(f"[cache-v4] {done}/{total} shards already exist; building {total-done}", flush=True)

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one_shard, j): j for j in jobs}
        finished = 0
        for fut in as_completed(futures):
            finished += 1
            res = fut.result()
            if res[0] == "ok":
                print(f"  [{finished}/{total}] OK {Path(res[1]).name} ({res[2]:.0f}s)", flush=True)
            elif res[0] == "skip":
                print(f"  [{finished}/{total}] SKIP {Path(res[1]).name}", flush=True)
            elif res[0] == "fail":
                print(f"  [{finished}/{total}] FAIL {Path(res[1]).name} ({res[2]:.0f}s): {res[3]}", flush=True)
            else:
                print(f"  [{finished}/{total}] TIMEOUT {Path(res[1]).name}", flush=True)

    # Concatenate
    print(f"\n[cache-v4] concatenating from {shard_dir}", flush=True)
    shards = sorted(shard_dir.glob("shard_*.parquet"))
    if not shards:
        print("[cache-v4] no shards"); sys.exit(1)
    print(f"  {len(shards)} shards")
    dfs = [pd.read_parquet(p) for p in shards]
    full = pd.concat(dfs, ignore_index=True)
    del dfs
    print(f"  concatenated: rows={len(full):,} cols={len(full.columns)}", flush=True)

    with session_scope() as s:
        cluster_rows = list(s.execute(
            select(TickerCluster.market, TickerCluster.ticker, TickerCluster.cluster_id)
        ).all())
    cluster_df = pd.DataFrame(cluster_rows, columns=["market", "ticker", "cluster_id"])
    full = full.merge(cluster_df, on=["market", "ticker"], how="left")

    print("  applying cross-section...", flush=True)
    full = apply_cross_section_features(full)

    full.to_parquet(final_path, index=False)
    size_mb = final_path.stat().st_size / 1024 / 1024
    print(f"[cache-v4] saved {final_path} ({size_mb:.1f} MB)", flush=True)


if __name__ == "__main__":
    main()
