"""Run EnsembleOptimizer over every cluster registered in the DB.

For each (market, cluster_id, target) we:
1. Build the feature matrix
2. Run forward-selection ensemble search on multi-window holdout
3. Append the chosen spec to var/models/ensembles.json

Usage:

    uv run python scripts/optimize_ensembles.py
    uv run python scripts/optimize_ensembles.py --markets US --target ret_fwd_21d
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import distinct, select

from core.db import session_scope
from core.models.training import TickerClusterAssignment as TickerCluster
from training.ensemble_optimizer import optimize
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel


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
    ap.add_argument("--markets", default="KR,US")
    ap.add_argument("--target", default="ret_fwd_21d")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--clusters",
                    help="comma list of cluster_ids to restrict to (default: all in DB)")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    markets = args.markets.split(",")
    print(f"[optimize] markets={markets} target={args.target} window={start}..{end}")

    summary: list[tuple[str, dict]] = []
    for market in markets:
        market = market.strip()
        with session_scope() as s:
            feat_df, _ = build_feature_matrix(
                s, market=market, start=start, end=end,
            )
            if feat_df.empty:
                print(f"  {market}: empty features; skip")
                continue
            close_panel = load_close_panel(
                s, market=market, start=start - timedelta(days=10), end=end,
            )
            feat_df = attach_labels(feat_df, close_panel)
            feat_df = attach_clusters(feat_df, s)
            cluster_ids = list(feat_df["cluster_id"].dropna().unique())

        if args.clusters:
            requested = set(args.clusters.split(","))
            cluster_ids = [c for c in cluster_ids if c in requested]

        print(f"  {market}: {len(cluster_ids)} cluster(s) to optimize")
        for cluster_id in sorted(cluster_ids):
            cluster_df = feat_df[feat_df["cluster_id"] == cluster_id]
            if len(cluster_df) < 100:
                print(f"    {cluster_id}: too small ({len(cluster_df)}); skip")
                continue
            spec, report = optimize(
                cluster_df, cluster_id=cluster_id, target=args.target,
            )
            if spec:
                members_str = "+".join(f"{m.model_kind}({w:.2f})"
                                       for m, w in zip(spec.members, spec.weights))
                print(f"    {cluster_id:<24s} "
                      f"IC={spec.holdout_ic_mean:+.4f}±{spec.holdout_ic_std:.3f} "
                      f"hit={spec.holdout_hit_mean:.3f} "
                      f"members={members_str}")
            else:
                print(f"    {cluster_id:<24s} IGNORE "
                      f"(best single IC={report.final_ic_mean:+.4f}) - {report.notes}")
            summary.append((cluster_id, {
                "ic": report.final_ic_mean,
                "members": report.final_members,
                "gate": report.chosen_gate,
            }))


if __name__ == "__main__":
    main()
