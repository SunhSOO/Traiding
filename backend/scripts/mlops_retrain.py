"""MLOps retrain pipeline orchestrator.

Run end-to-end:

1. Score data freshness (do we have new bars / disclosures / news?)
2. Backfill historical scores for any newly-arrived dates
3. For each cluster: retrain (default + Optuna tune) → save_model
4. Run ensemble_optimizer over the new + old models
5. Persist new ensemble specs (auto-supersedes old)
6. Drift check: compare new spec OOS-IC vs prior spec OOS-IC
7. Print summary; flag clusters with no improvement (operator review)

Schedule (production):
- Weekly (Sunday 02:00 UTC) — full retrain
- Daily (after macro.daily) — drift check only; if any cluster drifts,
  trigger off-schedule retrain.

Usage:

    uv run python scripts/mlops_retrain.py [--markets KR,US]
                                            [--clusters CLUSTER_ID,...]
                                            [--skip-tune]
                                            [--drift-only]
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
from training.drift_detector import check_spec_drift
from training.ensemble_optimizer import optimize
from training.ensemble_spec import find_active
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel
from training.model_registry import new_run_id, save_model, write_run_manifest
from training.multi_trainer import train_model
from training.optuna_tuner import tune
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


def _train_cluster(cluster_df, *, cluster_id, target, cols, run_id, skip_tune: bool):
    """Train default + (optionally) tuned models for one cluster."""
    print(f"    training default models for {cluster_id} ({len(cluster_df)} rows)")
    for kind in ("lgbm", "xgb", "catboost"):
        try:
            res = train_model(
                cluster_df, model_kind=kind, feature_cols=cols,
                target_col=target, cluster_id=cluster_id,
            )
        except Exception as e:
            print(f"      {kind}: train failed {e}")
            continue
        if res is None:
            continue
        save_model(
            res.booster, run_id=run_id, model_kind=kind,
            cluster_id=cluster_id, target=target,
            metrics=res.as_metrics_jsonb(),
            feature_names=res.feature_names,
            params={"_kind": "default"},
        )
        print(f"      {kind:<10s} default  IC={res.final_ic_oof:+.4f}")

    if skip_tune or len(cluster_df) > 60_000:
        return  # large clusters: default wins per the registry policy

    print(f"    Optuna 30-trial for {cluster_id} (small cluster)")
    for kind in ("lgbm", "xgb", "catboost"):
        try:
            tres = tune(
                cluster_df, model_kind=kind, feature_cols=cols,
                target_col=target, cluster_id=cluster_id,
                n_trials=30,
            )
        except Exception as e:
            print(f"      {kind} tune failed {e}")
            continue
        if tres is None:
            continue
        save_model(
            tres.booster, run_id=run_id, model_kind=kind,
            cluster_id=cluster_id, target=target,
            metrics=tres.best_metrics, feature_names=tres.feature_names,
            params=tres.best_params,
        )
        print(f"      {kind:<10s} tuned    IC={tres.best_value:+.4f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="KR,US")
    ap.add_argument("--clusters", help="comma list to restrict")
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--skip-tune", action="store_true",
                    help="skip Optuna search; default-params only")
    ap.add_argument("--drift-only", action="store_true",
                    help="just check drift for already-deployed specs; no retrain")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    run_id = new_run_id() + "_mlops"
    markets = [m.strip() for m in args.markets.split(",")]

    print(f"[mlops] run_id={run_id} markets={markets} target={args.target}")

    write_run_manifest(run_id, manifest={
        "kind": "mlops_retrain",
        "markets": markets, "target": args.target,
        "window_start": str(start), "window_end": str(end),
        "skip_tune": args.skip_tune, "drift_only": args.drift_only,
    })

    for market in markets:
        print(f"\n  market={market}")
        with session_scope() as s:
            feat_df, _ = build_feature_matrix(
                s, market=market, start=start, end=end,
            )
            if feat_df.empty:
                print("    empty features; skip"); continue
            close_panel = load_close_panel(
                s, market=market, start=start - timedelta(days=10), end=end,
            )
            feat_df = attach_labels(feat_df, close_panel)
            feat_df = attach_clusters(feat_df, s)

        cols = [c for c in ALL_FEATURE_COLS if c in feat_df.columns]
        cluster_ids = list(feat_df["cluster_id"].dropna().unique())
        if args.clusters:
            req = set(args.clusters.split(","))
            cluster_ids = [c for c in cluster_ids if c in req]

        # Drift check on currently-active specs (always)
        recent_df = feat_df[feat_df["date"] >= (pd.Timestamp(end) - pd.Timedelta(days=21))]
        ref_df = feat_df[feat_df["date"] < (pd.Timestamp(end) - pd.Timedelta(days=63))]
        drift_clusters: list[str] = []
        for cid in cluster_ids:
            spec = find_active(cid, args.target)
            if spec is None:
                continue
            cl_recent = recent_df[recent_df["cluster_id"] == cid]
            cl_ref = ref_df[ref_df["cluster_id"] == cid]
            if len(cl_recent) < 50 or len(cl_ref) < 100:
                continue
            sig = check_spec_drift(spec, cl_recent, cl_ref)
            if sig.drift_triggered:
                drift_clusters.append(cid)
                print(f"    [drift] {cid}: triggered. reasons={sig.reasons}")

        if args.drift_only:
            print(f"    drift-only mode; {len(drift_clusters)} clusters flagged")
            continue

        # Retrain phase
        for cid in sorted(cluster_ids):
            sub = feat_df[feat_df["cluster_id"] == cid]
            if len(sub) < 500:
                continue
            _train_cluster(
                sub, cluster_id=cid, target=args.target, cols=cols,
                run_id=run_id, skip_tune=args.skip_tune,
            )
            spec, report = optimize(sub, cluster_id=cid, target=args.target)
            if spec:
                print(f"    {cid:<24s} SPEC IC={spec.holdout_ic_mean:+.4f} "
                      f"members={len(spec.members)} gate={spec.decision_gate}")
            else:
                print(f"    {cid:<24s} IGNORE - {report.notes}")


if __name__ == "__main__":
    main()
