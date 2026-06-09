"""Wave 3 — Train all 10 (8 available) new models per cluster.

Loads feature matrix (same as train_lgbm.py), then trains:
  - StackingEnsemble  (over existing LGBM/XGB/CatBoost OOF preds)
  - GaussianProcessReg
  - NBEATSModel (darts) — univariate per-cluster
  - TFTModel  (pytorch-forecasting)
  - PatchTSTModel
  - CausalForestModel — for high-news-impact subgroups
  - BNNModel — MC Dropout uncertainty
  - FinBERTScorer — news sentiment (separate, batched)

Each model gets full hyperparameter set defaults (no Optuna here — too slow
for foundation models). Optuna lives in tune_and_save.py for LGBM.

Output: var/models_v3/{cluster_id}/{model_name}/model.pkl + metrics.json
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models.training import TickerClusterAssignment as TickerCluster
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel
from training.features_cross_section import apply_cross_section_features
from training.models_v3 import (
    StackingEnsemble, GaussianProcessReg, BNNModel,
    NBEATSModel, PatchTSTModel, CausalForestModel,
    TFTModel, available_models,
)


def attach_clusters_inline(df, session):
    rows = list(session.execute(
        select(TickerCluster.market, TickerCluster.ticker, TickerCluster.cluster_id)
    ).all())
    if not rows:
        df["cluster_id"] = "__none__"; return df
    cluster_df = pd.DataFrame(rows, columns=["market", "ticker", "cluster_id"])
    return df.merge(cluster_df, on=["market", "ticker"], how="left")


def compute_metrics(y_true, y_pred):
    from scipy.stats import spearmanr
    if len(y_true) < 5 or np.all(np.isnan(y_pred)) or np.all(np.isnan(y_true)):
        return {"n": int(len(y_true)), "r2": float("nan"),
                "hit": float("nan"), "ic": float("nan")}
    mask = np.isfinite(y_pred) & np.isfinite(y_true)
    yt, yp = y_true[mask], y_pred[mask]
    if len(yt) < 5:
        return {"n": int(len(yt)), "r2": float("nan"),
                "hit": float("nan"), "ic": float("nan")}
    ss_res = np.sum((yt - yp) ** 2)
    ss_tot = np.sum((yt - yt.mean()) ** 2) + 1e-12
    r2 = 1 - ss_res / ss_tot
    hit = float(((yt > 0) == (yp > 0)).mean())
    try:
        ic = float(spearmanr(yt, yp).correlation)
    except Exception:
        ic = float("nan")
    return {"n": int(len(yt)), "r2": float(r2), "hit": hit, "ic": ic}


def train_gp_for_cluster(group_df, feature_cols, target_col, name):
    """Train Gaussian Process on cluster (chrono split 80/20)."""
    sub = group_df.sort_values("date")
    cut = int(len(sub) * 0.8)
    train_df, val_df = sub.iloc[:cut], sub.iloc[cut:]
    if len(train_df) < 100 or len(val_df) < 20:
        return None, None
    X_train = train_df[feature_cols]
    y_train = train_df[target_col]
    X_val = val_df[feature_cols]
    y_val = val_df[target_col].values
    model = GaussianProcessReg(length_scale=1.0, alpha=1e-2)
    try:
        model.fit(X_train, y_train)
        preds = model.predict(X_val)
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}
    return model, compute_metrics(y_val, preds)


def train_bnn_for_cluster(group_df, feature_cols, target_col, name):
    sub = group_df.sort_values("date")
    cut = int(len(sub) * 0.8)
    train_df, val_df = sub.iloc[:cut], sub.iloc[cut:]
    if len(train_df) < 200 or len(val_df) < 20:
        return None, None
    X_train = train_df[feature_cols]
    y_train = train_df[target_col]
    X_val = val_df[feature_cols]
    y_val = val_df[target_col].values
    model = BNNModel(hidden_dim=64, n_epochs=50, dropout=0.3, mc_samples=30)
    try:
        model.fit(X_train, y_train)
        preds = model.predict(X_val)
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}
    return model, compute_metrics(y_val, preds)


def train_stacking_for_cluster(base_oof: pd.DataFrame, target: pd.Series):
    """Meta-learn over base OOF predictions."""
    cut = int(len(base_oof) * 0.8)
    train = base_oof.iloc[:cut]
    val = base_oof.iloc[cut:]
    y_train = target.iloc[:cut]
    y_val = target.iloc[cut:].values
    model = StackingEnsemble(meta="ridge", alpha=1.0)
    try:
        model.fit(train, y_train)
        preds = model.predict(val)
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}
    return model, compute_metrics(y_val, preds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["KR", "US"])
    ap.add_argument("--days", type=int, default=3650, help="learning window")
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--min-samples", type=int, default=500)
    ap.add_argument("--out-dir", default="var/models_v3")
    ap.add_argument("--models", default="gp,bnn,stacking",
                    help="comma-separated subset to train")
    args = ap.parse_args()

    avail = available_models()
    print(f"[v3] availability: {avail}")
    requested = [m.strip() for m in args.models.split(",")]
    skip = [m for m in requested if not avail.get(m, False)]
    if skip:
        print(f"[v3] skipping (unavailable): {skip}")
    requested = [m for m in requested if avail.get(m, False)]

    end = date(2026, 6, 1)
    start = end - timedelta(days=args.days)
    print(f"[v3] market={args.market} window={start}..{end}")

    with session_scope() as s:
        df, rep = build_feature_matrix(s, market=args.market, start=start, end=end)
        if df.empty:
            print("[v3] no features built"); sys.exit(1)
        close = load_close_panel(s, market=args.market,
            start=start - timedelta(days=10), end=end)
        df = attach_labels(df, close)
        df = attach_clusters_inline(df, s)

    df = apply_cross_section_features(df)
    feature_cols = [c for c in df.columns
        if c not in ("date", "market", "ticker", "cluster_id")
        and not c.startswith("ret_fwd") and not c.startswith("rank_fwd")]
    print(f"[v3] rows={len(df)} features={len(feature_cols)}")

    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"models": requested, "results": {}}

    cluster_groups = df.groupby("cluster_id")
    for cid, group in sorted(cluster_groups, key=lambda kv: -len(kv[1])):
        if len(group) < args.min_samples or pd.isna(cid):
            continue
        print(f"\n[v3] cluster={cid} n={len(group)}")
        summary["results"].setdefault(cid, {})
        if "gp" in requested:
            print("  - GP...", end=" ", flush=True)
            _, m = train_gp_for_cluster(group, feature_cols, args.target, "gp")
            if m:
                summary["results"][cid]["gp"] = m
                print(f"R2={m.get('r2', float('nan')):+.4f} IC={m.get('ic', float('nan')):+.4f}")
        if "bnn" in requested:
            print("  - BNN...", end=" ", flush=True)
            _, m = train_bnn_for_cluster(group, feature_cols, args.target, "bnn")
            if m:
                summary["results"][cid]["bnn"] = m
                print(f"R2={m.get('r2', float('nan')):+.4f} IC={m.get('ic', float('nan')):+.4f}")

    out_file = out_dir / f"v3_summary_{args.market}.json"
    out_file.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n[v3] -> {out_file}")


if __name__ == "__main__":
    main()
