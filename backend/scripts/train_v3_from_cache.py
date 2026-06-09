"""Wave 3 trainer using cached feature matrix — Wave 3.

Same as train_models_v3.py but loads parquet cache instead of rebuilding.

Usage:
    uv run python scripts/train_v3_from_cache.py --market US --models gp,bnn,stacking
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from training.models_v3 import (
    GaussianProcessReg, BNNModel, StackingEnsemble, available_models,
)
from scripts.train_lgbm import ALL_FEATURE_COLS


def find_cache(market: str, cache_dir: Path) -> Path | None:
    candidates = sorted(cache_dir.glob(f"features_{market}_*.parquet"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def compute_metrics(y_true, y_pred):
    from scipy.stats import spearmanr
    mask = np.isfinite(y_pred) & np.isfinite(y_true)
    yt, yp = y_true[mask], y_pred[mask]
    if len(yt) < 5:
        return {"n": int(len(yt)), "r2": float("nan"), "hit": float("nan"), "ic": float("nan")}
    ss_res = np.sum((yt - yp) ** 2)
    ss_tot = np.sum((yt - yt.mean()) ** 2) + 1e-12
    r2 = 1 - ss_res / ss_tot
    hit = float(((yt > 0) == (yp > 0)).mean())
    try:
        ic = float(spearmanr(yt, yp).correlation)
    except Exception:
        ic = float("nan")
    return {"n": int(len(yt)), "r2": float(r2), "hit": hit, "ic": ic}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--models", default="gp,bnn,stacking")
    ap.add_argument("--min-samples", type=int, default=500)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", default="var/v3_results.json")
    ap.add_argument("--cache-dir", default="var/cache")
    args = ap.parse_args()

    avail = available_models()
    requested = [m.strip() for m in args.models.split(",")]
    requested = [m for m in requested if avail.get(m, False)]
    print(f"[v3] available models: {requested}")

    cache = find_cache(args.market, Path(args.cache_dir))
    if cache is None:
        print(f"[v3] no cache in {args.cache_dir}"); sys.exit(1)
    print(f"[v3] loading {cache}")
    t0 = time.time()
    df = pd.read_parquet(cache)
    print(f"  rows={len(df):,} cols={len(df.columns)} ({time.time()-t0:.1f}s)")

    feature_cols = [c for c in ALL_FEATURE_COLS if c in df.columns]
    results = {"market": args.market, "models": requested,
                "n_features": len(feature_cols), "results": {}}

    clusters = df["cluster_id"].value_counts().head(args.top).index.tolist()
    for cid in clusters:
        sub = df[df["cluster_id"] == cid].sort_values("date").copy()
        if len(sub) < args.min_samples:
            continue
        cut = int(len(sub) * 0.8)
        train, val = sub.iloc[:cut], sub.iloc[cut:]
        if len(val) < 30:
            continue
        X_tr, y_tr = train[feature_cols], train[args.target]
        X_v, y_v = val[feature_cols], val[args.target].values
        # Drop rows with NaN target
        mask_tr = y_tr.notna()
        X_tr, y_tr = X_tr[mask_tr], y_tr[mask_tr]
        mask_v = pd.Series(y_v).notna().values
        X_v_clean = X_v[mask_v]
        y_v_clean = y_v[mask_v]
        if len(y_tr) < 100 or len(y_v_clean) < 20:
            continue

        print(f"\n[v3] cluster={cid} train={len(y_tr)} val={len(y_v_clean)}")
        results["results"][cid] = {}

        if "gp" in requested:
            try:
                t1 = time.time()
                m = GaussianProcessReg(length_scale=1.0, alpha=1e-2)
                m.fit(X_tr, y_tr)
                pred = m.predict(X_v_clean)
                metrics = compute_metrics(y_v_clean, pred)
                metrics["elapsed"] = round(time.time() - t1, 1)
                results["results"][cid]["gp"] = metrics
                print(f"  GP: R2={metrics['r2']:+.4f} IC={metrics['ic']:+.4f} ({metrics['elapsed']}s)")
            except Exception as e:
                print(f"  GP: FAIL {type(e).__name__}: {e}")

        if "bnn" in requested:
            try:
                t1 = time.time()
                m = BNNModel(hidden_dim=64, n_epochs=50, dropout=0.3, mc_samples=30)
                m.fit(X_tr, y_tr)
                pred = m.predict(X_v_clean)
                metrics = compute_metrics(y_v_clean, pred)
                metrics["elapsed"] = round(time.time() - t1, 1)
                results["results"][cid]["bnn"] = metrics
                print(f"  BNN: R2={metrics['r2']:+.4f} IC={metrics['ic']:+.4f} ({metrics['elapsed']}s)")
            except Exception as e:
                print(f"  BNN: FAIL {type(e).__name__}: {e}")

        # Stacking requires base predictions from LGBM/XGB/CatBoost — defer to ensemble script
        Path(args.out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    Path(args.out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\n[v3] -> {args.out}")


if __name__ == "__main__":
    main()
