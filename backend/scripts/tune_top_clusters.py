"""Auto-discover top clusters by sample size + run Optuna 200-trial — Wave 3.

Wrapper around tune_and_save.py that:
  1. Queries ticker_clusters to find largest clusters
  2. For each top-K cluster, spawns tune_and_save.py with --trials 200

Usage:
    uv run python scripts/tune_top_clusters.py --top 10 --trials 200 --market US
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, func
from core.db import session_scope
from core.models.training import TickerClusterAssignment as TickerCluster


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["KR", "US"])
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--days", type=int, default=3650, help="10y")
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--models", default="lgbm,xgb,catboost")
    args = ap.parse_args()

    with session_scope() as s:
        rows = list(s.execute(
            select(TickerCluster.cluster_id, func.count())
            .where(TickerCluster.cluster_id.isnot(None))
            .group_by(TickerCluster.cluster_id)
            .order_by(func.count().desc())
            .limit(args.top)
        ).all())
    top_clusters = [r[0] for r in rows]
    print(f"[tune] top {len(top_clusters)} clusters: {top_clusters}")

    for cid in top_clusters:
        print(f"\n[tune] cluster={cid} trials={args.trials} days={args.days}")
        cmd = [
            "uv", "run", "python", "scripts/tune_and_save.py",
            "--market", args.market, "--cluster", cid,
            "--target", args.target, "--trials", str(args.trials),
            "--days", str(args.days), "--models", args.models,
        ]
        r = subprocess.run(cmd, capture_output=False)
        if r.returncode != 0:
            print(f"[tune] cluster={cid} FAILED rc={r.returncode}")


if __name__ == "__main__":
    main()
