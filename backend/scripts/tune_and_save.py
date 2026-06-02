"""Hyperparameter tuning + model persistence pipeline.

For one (market, cluster, target) tuple:
1. Build feature matrix
2. For each model_kind in {lgbm, xgb, catboost}:
   - Run Optuna with N trials
   - Save the best model to the registry
3. Build an inverse-RMSE weighted ensemble across the 3 best models
4. Re-evaluate the ensemble OOF and save its metrics

Usage:

    uv run python scripts/tune_and_save.py \
        --market KR --cluster KR:FIN:LARGE --trials 30
    uv run python scripts/tune_and_save.py \
        --market US --cluster __global__ --trials 30
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models.training import TickerClusterAssignment as TickerCluster
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel
from training.model_registry import (
    new_run_id, save_model, write_run_manifest,
)
from training.multi_trainer import train_model
from training.optuna_tuner import tune
from scripts.train_lgbm import ALL_FEATURE_COLS


def attach_clusters(df: pd.DataFrame, session) -> pd.DataFrame:
    rows = list(session.execute(
        select(TickerCluster.market, TickerCluster.ticker, TickerCluster.cluster_id)
    ).all())
    if not rows:
        df["cluster_id"] = "__none__"
        return df
    c = pd.DataFrame(rows, columns=["market", "ticker", "cluster_id"])
    return df.merge(c, on=["market", "ticker"], how="left")


def _ic(y_true, y_pred) -> float:
    if len(y_true) < 2:
        return 0.0
    rt = pd.Series(y_true).rank().values
    rp = pd.Series(y_pred).rank().values
    if np.std(rt) == 0 or np.std(rp) == 0:
        return 0.0
    return float(np.corrcoef(rt, rp)[0, 1])


def _hit(y_true, y_pred) -> float:
    return float(((y_true >= 0) == (y_pred >= 0)).mean()) if len(y_true) else 0.0


def _r2(y_true, y_pred) -> float:
    y_true = np.asarray(y_true); y_pred = np.asarray(y_pred)
    if len(y_true) < 2:
        return 0.0
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    return 0.0 if ss_tot <= 0 else 1.0 - ss_res / ss_tot


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--cluster", default="__global__")
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--models", default="lgbm,xgb,catboost")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    run_id = new_run_id()
    print(f"[tune] run_id={run_id} market={args.market} cluster={args.cluster} "
          f"target={args.target} trials={args.trials}")

    with session_scope() as s:
        feat_df, rep = build_feature_matrix(
            s, market=args.market, start=start, end=end,
        )
        if feat_df.empty:
            print("  empty feature matrix; abort")
            sys.exit(1)
        close_panel = load_close_panel(s, market=args.market,
                                        start=start - timedelta(days=10), end=end)
        feat_df = attach_labels(feat_df, close_panel)
        feat_df = attach_clusters(feat_df, s)

    cols = [c for c in ALL_FEATURE_COLS if c in feat_df.columns]
    if args.cluster == "__global__":
        train_df = feat_df
    else:
        train_df = feat_df[feat_df["cluster_id"] == args.cluster]
    print(f"  rows={len(train_df)} features={len(cols)}")

    write_run_manifest(run_id, manifest={
        "market": args.market,
        "cluster": args.cluster,
        "target": args.target,
        "window_start": str(start),
        "window_end": str(end),
        "n_rows": int(len(train_df)),
        "n_features": len(cols),
        "trials_per_model": args.trials,
    })

    tuned_results = {}
    for kind in args.models.split(","):
        kind = kind.strip()
        print(f"\n  ---- tuning {kind} ----")
        res = tune(
            train_df, model_kind=kind, feature_cols=cols,
            target_col=args.target, cluster_id=args.cluster,
            n_trials=args.trials,
        )
        if res is None:
            print(f"  {kind}: returned None")
            continue
        print(f"  best IC={res.best_value:+.4f} R²={res.best_metrics['final_r2_oof']:+.4f} "
              f"hit={res.best_metrics['final_hit_rate_oof']:.3f} "
              f"params(top): {dict(list(res.best_params.items())[:5])}")
        artifact = save_model(
            res.booster, run_id=run_id, model_kind=kind,
            cluster_id=args.cluster, target=args.target,
            metrics=res.best_metrics, feature_names=res.feature_names,
            params=res.best_params,
        )
        print(f"  saved -> {artifact.model_path}")
        tuned_results[kind] = res

    # Print summary
    print("\n  ==== Tuning Summary ====")
    print(f"  {'model':<10s} {'IC_oof':>9s} {'R²_oof':>9s} {'hit':>7s} {'RMSE':>9s}")
    for kind, res in tuned_results.items():
        m = res.best_metrics
        print(f"  {kind:<10s} {m['final_ic_oof']:>+9.4f} {m['final_r2_oof']:>+9.4f} "
              f"{m['final_hit_rate_oof']:>7.3f} {m['final_rmse_oof']:>9.4f}")

    # TODO: build inverse-RMSE ensemble. Needs OOF preds across folds —
    # for now we just record per-model. Ensemble compare is a follow-up
    # that loads multiple models and re-evaluates on a hold-out fold.


if __name__ == "__main__":
    main()
