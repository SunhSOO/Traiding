"""Feature-selection A/B — does pruning to top-K features cut overfitting?

Motivation: with all 462 features, KR ret_fwd_21d OOF IC went NEGATIVE
(-0.044) despite 60% hit-rate — a classic too-many-features overfit on a
noisy target. This script builds the matrix ONCE (and caches it to parquet
for fast re-runs), then trains `train_one` with:
  A) all available features
  B) top-K by importance (from A)
  C) top-K + stronger regularisation
and prints OOF IC / hit / R² for each target so we pick empirically.

Usage:
    uv run python scripts/feature_selection_ab.py --market KR --days 365 --topk 50
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.db import session_scope
from training.features import build_feature_matrix
from training.features_cross_section import apply_cross_section_features
from training.labels_multi import attach_labels, load_close_panel
from training.lgbm_trainer import train_one
from scripts.train_lgbm import ALL_FEATURE_COLS


def _metrics(r) -> dict:
    if r is None:
        return {}
    return {
        "ic": round(getattr(r, "final_ic_oof", float("nan")), 4),
        "hit": round(getattr(r, "final_hit_rate_oof", float("nan")), 4),
        "r2": round(getattr(r, "final_r2_oof", float("nan")), 4),
        "n": getattr(r, "n_samples", None),
    }


def _importance(r) -> dict:
    for attr in ("top_features", "feature_importance", "importances"):
        v = getattr(r, attr, None)
        if isinstance(v, dict) and v:
            return v
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--topk", type=int, default=50)
    args = ap.parse_args()

    cache = Path(f"var/_fs_ab_{args.market}_{args.days}.parquet")
    raw_cache = Path(f"var/_fs_raw_{args.market}_{args.days}.parquet")
    if cache.exists():
        print(f"[fs-ab] loading prepared matrix {cache}", flush=True)
        feat_df = pd.read_parquet(cache)
    else:
        end = date.today()
        start = end - timedelta(days=args.days)
        if raw_cache.exists():
            print(f"[fs-ab] loading raw build {raw_cache} (skip rebuild)", flush=True)
            feat_df = pd.read_parquet(raw_cache)
        else:
            print(f"[fs-ab] building {args.market} {start}..{end} ...", flush=True)
            with session_scope() as s:
                feat_df, rep = build_feature_matrix(s, market=args.market, start=start, end=end)
            feat_df.to_parquet(raw_cache)  # preserve the expensive build immediately
            print(f"[fs-ab] raw build rows={len(feat_df)} -> {raw_cache}", flush=True)
        with session_scope() as s:
            close_panel = load_close_panel(s, market=args.market,
                                           start=start - timedelta(days=10), end=end)
        feat_df = attach_labels(feat_df, close_panel)
        feat_df = apply_cross_section_features(feat_df)
        feat_df.to_parquet(cache)
        print(f"[fs-ab] prepared rows={len(feat_df)} cols={feat_df.shape[1]} -> cached", flush=True)

    avail = [c for c in ALL_FEATURE_COLS if c in feat_df.columns]
    print(f"[fs-ab] available features: {len(avail)}", flush=True)

    reg_params = {  # stronger regularisation
        "num_leaves": 15, "max_depth": 4, "min_child_samples": 200,
        "reg_alpha": 1.0, "reg_lambda": 5.0, "feature_fraction": 0.5,
        "bagging_fraction": 0.7, "bagging_freq": 1, "learning_rate": 0.02,
    }

    for target in ("ret_fwd_21d", "rank_fwd_21d"):
        print(f"\n===== target: {target} =====", flush=True)
        r_all = train_one(feat_df, feature_cols=avail, target_col=target,
                          cluster_id="__all__")
        imp = _importance(r_all)
        topk = [f for f, _ in sorted(imp.items(), key=lambda kv: kv[1], reverse=True)][:args.topk]
        topk = [c for c in topk if c in avail] or avail[:args.topk]
        r_top = train_one(feat_df, feature_cols=topk, target_col=target,
                          cluster_id="__topk__")
        r_reg = train_one(feat_df, feature_cols=topk, target_col=target,
                          cluster_id="__topk_reg__", params=reg_params)
        print(f"  A) all {len(avail):>3}feat : {_metrics(r_all)}", flush=True)
        print(f"  B) top{args.topk:<3}      : {_metrics(r_top)}", flush=True)
        print(f"  C) top{args.topk}+reg    : {_metrics(r_reg)}", flush=True)


if __name__ == "__main__":
    main()
