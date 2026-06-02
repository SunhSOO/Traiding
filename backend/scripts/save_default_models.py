"""Train + save models with DEFAULT params for the registry.

Why: in some clusters the default-param model beats the Optuna-tuned
one (KR:FIN:LARGE is the canonical example — IC 0.262 default vs 0.180
tuned). The registry should keep both so the ensemble can pick.

Usage:

    uv run python scripts/save_default_models.py --market KR --cluster KR:FIN:LARGE
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models.training import TickerClusterAssignment as TickerCluster
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel
from training.model_registry import new_run_id, save_model, write_run_manifest
from training.multi_trainer import train_model
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
    ap.add_argument("--cluster", default="__global__")
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--models", default="lgbm,xgb,catboost")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    run_id = new_run_id() + "_default"
    print(f"[default] run_id={run_id} market={args.market} cluster={args.cluster}")

    with session_scope() as s:
        feat_df, _ = build_feature_matrix(
            s, market=args.market, start=start, end=end,
        )
        close_panel = load_close_panel(
            s, market=args.market, start=start - timedelta(days=10), end=end,
        )
        feat_df = attach_labels(feat_df, close_panel)
        feat_df = attach_clusters(feat_df, s)

    cols = [c for c in ALL_FEATURE_COLS if c in feat_df.columns]
    if args.cluster == "__global__":
        train_df = feat_df
    else:
        train_df = feat_df[feat_df["cluster_id"] == args.cluster]
    print(f"  rows={len(train_df)} features={len(cols)}")

    write_run_manifest(run_id, manifest={
        "market": args.market, "cluster": args.cluster, "target": args.target,
        "window_start": str(start), "window_end": str(end),
        "n_rows": int(len(train_df)), "n_features": len(cols),
        "kind": "default_params",
    })

    print(f"\n  {'model':<10s} {'IC':>9s} {'R²':>9s} {'hit':>7s}")
    for kind in args.models.split(","):
        kind = kind.strip()
        res = train_model(
            train_df, model_kind=kind, feature_cols=cols,
            target_col=args.target, cluster_id=args.cluster,
        )
        if res is None:
            print(f"  {kind:<10s} (None)")
            continue
        m = res.as_metrics_jsonb()
        print(f"  {kind:<10s} {m['final_ic_oof']:>+9.4f} {m['final_r2_oof']:>+9.4f} "
              f"{m['final_hit_rate_oof']:>7.3f}")
        save_model(
            res.booster, run_id=run_id, model_kind=kind,
            cluster_id=args.cluster, target=args.target,
            metrics=m, feature_names=res.feature_names,
            params={"_kind": "default"},
        )


if __name__ == "__main__":
    main()
