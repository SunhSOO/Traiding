"""Optuna tuning from cached feature matrix — Wave 3.

Loads var/cache/features_{market}_{from}_{to}.parquet (built by
cache_feature_matrix.py) instead of rebuilding. ~100x faster startup.

For each top-K cluster, runs Optuna `n_trials` per model (LGBM/XGB/CatBoost).

Usage:
    uv run python scripts/tune_from_cache.py --market US --top 10 --trials 100
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.optuna_tuner import tune
from training.registry import new_run_id, write_run_manifest, save_tuned_model
from scripts.train_lgbm import ALL_FEATURE_COLS


def find_cache(market: str, days: int, cache_dir: Path) -> Path | None:
    """Find the most recent cached parquet for given market+window."""
    candidates = sorted(cache_dir.glob(f"features_{market}_*.parquet"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--trials", type=int, default=100)
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--models", default="lgbm,xgb,catboost")
    ap.add_argument("--cache-dir", default="var/cache")
    ap.add_argument("--min-rows", type=int, default=200)
    ap.add_argument("--out", default="var/optuna_results.json")
    args = ap.parse_args()

    cache_path = find_cache(args.market, args.days if hasattr(args, "days") else 0,
                              Path(args.cache_dir))
    if cache_path is None or not cache_path.exists():
        print(f"[tune] no cache found in {args.cache_dir}"); sys.exit(1)
    print(f"[tune] loading cache {cache_path} ({cache_path.stat().st_size/1024/1024:.1f} MB)")
    t0 = time.time()
    df = pd.read_parquet(cache_path)
    print(f"  loaded: rows={len(df):,} cols={len(df.columns)} ({time.time()-t0:.1f}s)")

    if "cluster_id" not in df.columns:
        print("[tune] no cluster_id in cache; abort"); sys.exit(1)

    cluster_sizes = df["cluster_id"].value_counts()
    top_clusters = cluster_sizes.head(args.top).index.tolist()
    print(f"\n[tune] top {len(top_clusters)} clusters:")
    for c in top_clusters:
        print(f"  {c}: {cluster_sizes[c]:,}")

    feature_cols = [c for c in ALL_FEATURE_COLS if c in df.columns]
    print(f"\n[tune] feature columns: {len(feature_cols)} / {len(ALL_FEATURE_COLS)}")

    results = {"market": args.market, "trials": args.trials,
                "n_features": len(feature_cols), "started_at": datetime.now(timezone.utc).isoformat(),
                "results": {}}

    for cid in top_clusters:
        sub = df[df["cluster_id"] == cid].copy()
        if len(sub) < args.min_rows:
            print(f"\n[tune] {cid}: skip (n={len(sub)} < {args.min_rows})"); continue
        print(f"\n[tune] === {cid} (n={len(sub):,}) ===")
        results["results"][cid] = {}

        for kind in args.models.split(","):
            kind = kind.strip()
            print(f"  ---- tuning {kind} (trials={args.trials}) ----")
            t1 = time.time()
            try:
                res = tune(
                    sub, model_kind=kind, feature_cols=feature_cols,
                    target_col=args.target, cluster_id=cid,
                    n_trials=args.trials,
                )
            except Exception as e:
                print(f"  {kind}: FAIL {type(e).__name__}: {e}")
                results["results"][cid][kind] = {"error": str(e)}
                continue
            if res is None:
                print(f"  {kind}: None"); continue
            elapsed = time.time() - t1
            row = {
                "best_r2_oof": res.best_r2_oof,
                "best_hit_rate_oof": res.best_hit_rate_oof,
                "best_ic_oof": res.best_ic_oof,
                "best_params": res.best_params,
                "n_samples": res.n_samples,
                "elapsed_sec": round(elapsed, 1),
            }
            results["results"][cid][kind] = row
            print(f"  {kind}: R2={res.best_r2_oof:+.4f} hit={res.best_hit_rate_oof:.3f} "
                  f"IC={res.best_ic_oof:+.4f} ({elapsed:.0f}s)")

        # Save incrementally
        Path(args.out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    print(f"\n[tune] -> {args.out}")
    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path(args.out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
