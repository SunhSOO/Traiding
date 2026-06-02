"""Train LSTM on the same feature matrix used by tree models.

Mirrors scripts/tune_and_save.py but uses lstm_trainer + a separate
search space. Saves to the same registry so ensemble.py can combine
tree and LSTM predictions.

Note: LSTM trains MUCH slower than trees (minutes per fold even on
GPU). For initial run we use default hyperparameters; later sweep
seq_len ∈ {15, 30, 60} and hidden_dim ∈ {64, 128, 256} via Optuna.

Usage:

    uv run python scripts/train_lstm.py --market US --cluster US:ENERGY:LARGE
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
from training.lstm_trainer import train_lstm
from training.model_registry import new_run_id, save_model, write_run_manifest
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--cluster", default="__global__")
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--seq-len", type=int, default=30)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--layers", type=int, default=2)
    ap.add_argument("--dropout", type=float, default=0.3)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=1024)
    ap.add_argument("--n-splits", type=int, default=4)
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    run_id = new_run_id()
    print(f"[lstm] run_id={run_id} market={args.market} cluster={args.cluster} "
          f"seq_len={args.seq_len} hidden={args.hidden}")

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
        "model_kind": "lstm",
        "market": args.market, "cluster": args.cluster, "target": args.target,
        "window_start": str(start), "window_end": str(end),
        "n_rows": int(len(train_df)), "n_features": len(cols),
        "seq_len": args.seq_len, "hidden_dim": args.hidden,
        "num_layers": args.layers, "dropout": args.dropout,
    })

    print("\n  ---- LSTM training ----")
    res = train_lstm(
        train_df, feature_cols=cols, target_col=args.target,
        cluster_id=args.cluster,
        seq_len=args.seq_len, hidden_dim=args.hidden,
        num_layers=args.layers, dropout=args.dropout,
        n_epochs=args.epochs, batch_size=args.batch_size,
        n_splits=args.n_splits,
    )
    if res is None:
        print("  LSTM returned None")
        sys.exit(1)

    print(f"\n  ==== LSTM Result ====")
    print(f"  IC={res.final_ic_oof:+.4f} R²={res.final_r2_oof:+.4f} "
          f"hit={res.final_hit_rate_oof:.3f} RMSE={res.final_rmse_oof:.4f} "
          f"n_seqs={res.n_samples}")

    artifact = save_model(
        res.booster, run_id=run_id, model_kind="lstm",
        cluster_id=args.cluster, target=args.target,
        metrics=res.as_metrics_jsonb(), feature_names=cols,
        params={"seq_len": args.seq_len, "hidden_dim": args.hidden,
                "num_layers": args.layers, "dropout": args.dropout},
    )
    print(f"  saved -> {artifact.model_path}")


if __name__ == "__main__":
    main()
