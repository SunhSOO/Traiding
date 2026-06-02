"""Train multiple model families on the same data and compare metrics.

Runs (LightGBM | XGBoost | CatBoost | Ridge baseline) over the same
feature matrix + date-grouped TS CV, then prints a side-by-side
comparison table.

Usage:

    uv run python scripts/compare_models.py --market US --days 365
"""
from __future__ import annotations

import argparse
import json
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
from training.multi_trainer import train_model
from scripts.train_lgbm import ALL_FEATURE_COLS


MODELS = ["lgbm", "xgb", "catboost", "ridge"]


def attach_clusters(df: pd.DataFrame, session) -> pd.DataFrame:
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
    ap.add_argument("--market", default="US", choices=["KR", "US"])
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--cluster", default="__global__",
                    help="cluster_id to train on, or '__global__' for full data")
    ap.add_argument("--min-cluster-n", type=int, default=2000,
                    help="if cluster has < n samples, skip per-cluster comparison")
    ap.add_argument("--out", default="var/model_compare.json")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    print(f"[compare] market={args.market} window={start}..{end} target={args.target}")

    with session_scope() as s:
        feat_df, rep = build_feature_matrix(
            s, market=args.market, start=start, end=end,
        )
        if feat_df.empty:
            print("  no features built; abort")
            sys.exit(1)
        print(f"  feature matrix: rows={rep.rows_emitted} tickers={rep.tickers_processed}")

        close_panel = load_close_panel(s, market=args.market,
                                        start=start - timedelta(days=10), end=end)
        feat_df = attach_labels(feat_df, close_panel)
        feat_df = attach_clusters(feat_df, s)

    cols = [c for c in ALL_FEATURE_COLS if c in feat_df.columns]
    print(f"  features active: {len(cols)}/{len(ALL_FEATURE_COLS)}")

    # Pick training subset
    if args.cluster == "__global__":
        train_df = feat_df
    else:
        train_df = feat_df[feat_df["cluster_id"] == args.cluster]
        if len(train_df) < args.min_cluster_n:
            print(f"  cluster {args.cluster} too small ({len(train_df)} < {args.min_cluster_n})")
            sys.exit(1)

    summary = {
        "market": args.market, "cluster": args.cluster, "target": args.target,
        "n_rows": len(train_df), "n_features": len(cols),
        "results": {},
    }

    print(f"\n  rows for training: {len(train_df)}")
    print(f"\n  {'model':<10s} {'n':>8s} {'R²_oof':>9s} {'hit':>7s} {'IC':>7s} {'RMSE':>9s}")
    print(f"  {'-'*10} {'-'*8} {'-'*9} {'-'*7} {'-'*7} {'-'*9}")

    for kind in MODELS:
        try:
            res = train_model(
                train_df, model_kind=kind,
                feature_cols=cols, target_col=args.target,
                cluster_id=args.cluster,
            )
        except Exception as e:
            print(f"  {kind:<10s} FAILED {type(e).__name__}: {e}")
            summary["results"][kind] = {"error": f"{type(e).__name__}: {e}"}
            continue
        if res is None:
            print(f"  {kind:<10s} (returned None)")
            continue
        print(
            f"  {kind:<10s} {res.n_samples:>8d} {res.final_r2_oof:>+9.4f} "
            f"{res.final_hit_rate_oof:>7.3f} {res.final_ic_oof:>+7.4f} "
            f"{res.final_rmse_oof:>9.4f}"
        )
        summary["results"][kind] = res.as_metrics_jsonb()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n  saved -> {out_path}")


if __name__ == "__main__":
    main()
