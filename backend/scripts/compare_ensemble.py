"""Compare single models vs IC-weighted ensemble on a held-out date range.

For a given (market, cluster, target):
1. Build feature matrix
2. Hold out the most recent 21 days (embargo applied) as out-of-sample test
3. For each registered model (lgbm/xgb/catboost/lstm), predict on holdout
4. Build IC-weighted ensemble from top-K models (default K=3)
5. Print comparison table

Note: This re-evaluates models on a HOLDOUT period that's after their
training window. If models were trained with CV ending at T-21, holdout
is [T-21, T]. We compute IC/hit/R² on this held-out window.

Usage:

    uv run python scripts/compare_ensemble.py \
        --market US --cluster US:ENERGY:LARGE
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models.training import TickerClusterAssignment as TickerCluster
from training.ensemble import (
    LoadedModel, _predict_one, ensemble_metrics, load_top_models,
)
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel
from scripts.train_lgbm import ALL_FEATURE_COLS


def attach_clusters(df, session):
    rows = list(session.execute(
        select(TickerCluster.market, TickerCluster.ticker, TickerCluster.cluster_id)
    ).all())
    if not rows:
        df["cluster_id"] = "__none__"
        return df
    c = pd.DataFrame(rows, columns=["market", "ticker", "cluster_id"])
    return df.merge(c, on=["market", "ticker"], how="left")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--cluster", required=True)
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--top-k", type=int, default=3)
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    print(f"[ensemble] market={args.market} cluster={args.cluster} target={args.target}")

    with session_scope() as s:
        feat_df, _ = build_feature_matrix(
            s, market=args.market, start=start, end=end,
        )
        close_panel = load_close_panel(
            s, market=args.market, start=start - timedelta(days=10), end=end,
        )
        feat_df = attach_labels(feat_df, close_panel)
        feat_df = attach_clusters(feat_df, s)

    df = feat_df[feat_df["cluster_id"] == args.cluster].copy()
    df = df.dropna(subset=[args.target])
    if df.empty:
        print("  no rows for cluster"); return

    # Use the last 21 days as held-out test
    df["date"] = pd.to_datetime(df["date"])
    cutoff = df["date"].max() - pd.Timedelta(days=21)
    train_df = df[df["date"] <= cutoff]
    test_df = df[df["date"] > cutoff]
    print(f"  train rows {len(train_df)}, test rows {len(test_df)} "
          f"(holdout {cutoff.date()} -> {df['date'].max().date()})")
    if len(test_df) < 10:
        print("  test set too small"); return

    cols = [c for c in ALL_FEATURE_COLS if c in df.columns]
    models = load_top_models(target=args.target, cluster_id=args.cluster, top_k=args.top_k)
    if not models:
        print("  no registered models for this (target, cluster)"); return
    print(f"  {len(models)} models loaded:")
    for m in models:
        e = m.entry
        print(f"    {e['model_kind']:<10s} run={e['run_id']} "
              f"OOF_IC={e.get('final_ic_oof', 0):+.4f}")

    # Predict per model — each uses ITS OWN feature_names (registry-stored).
    # Different runs may have different feature counts (registry stores names per model).
    y_test = test_df[args.target].astype(float).values

    print(f"\n  {'model':<12s} {'R²':>9s} {'hit':>7s} {'IC':>7s} {'RMSE':>9s}")
    preds = []
    weights = []
    for m in models:
        feat_cols = m.entry["feature_names"]
        missing = [c for c in feat_cols if c not in test_df.columns]
        if missing:
            print(f"  {m.entry['model_kind']:<12s} SKIP — features missing: {missing[:3]}...")
            continue
        X_test = test_df[feat_cols].astype(float).values
        try:
            pred = _predict_one(m.entry["model_kind"], m.booster, X_test, feat_cols)
        except Exception as e:
            print(f"  {m.entry['model_kind']:<12s} FAILED {e}")
            continue
        metrics = ensemble_metrics(y_test, pred)
        print(f"  {m.entry['model_kind']:<12s} {metrics['r2']:>+9.4f} "
              f"{metrics['hit']:>7.3f} {metrics['ic']:>+7.4f} {metrics['rmse']:>9.4f}")
        preds.append(pred)
        weights.append(max(m.entry.get("final_ic_oof", 0.0), 0.0))

    if not preds:
        return
    P = np.stack(preds)
    w = np.array(weights, dtype="float64")
    if w.sum() <= 0:
        w = np.ones_like(w)
    w = w / w.sum()
    ensemble_pred = (P * w[:, None]).sum(axis=0)
    metrics = ensemble_metrics(y_test, ensemble_pred)
    print(f"  {'ENSEMBLE':<12s} {metrics['r2']:>+9.4f} "
          f"{metrics['hit']:>7.3f} {metrics['ic']:>+7.4f} {metrics['rmse']:>9.4f}")
    print(f"  weights: {dict(zip([m.entry['model_kind'] for m in models], w.round(3).tolist()))}")


if __name__ == "__main__":
    main()
