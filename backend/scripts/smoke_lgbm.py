"""LightGBM pipeline smoke test on a small slice.

Verifies the full chain (features → labels → train) works before
committing to the full 365-day run.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from core.db import session_scope
from core.models.universe import Security
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel
from training.lgbm_trainer import train_one

SP500_PILOT = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    "META", "TSLA", "AVGO", "JPM", "BAC",
    "WMT", "HD", "UNH", "LLY", "V",
    "MA", "PG", "XOM", "AMD", "INTC",
]


from scripts.train_lgbm import ALL_FEATURE_COLS as FEATURES


def main() -> None:
    end = date.today()
    start = end - timedelta(days=90)
    print(f"smoke: market=US tickers={len(SP500_PILOT)} window={start}..{end}")

    with session_scope() as s:
        feat_df, rep = build_feature_matrix(
            s, market="US", start=start, end=end, tickers=SP500_PILOT,
        )
        print(f"  features built: rows={rep.rows_emitted} cols={rep.feature_columns}")
        close_panel = load_close_panel(s, market="US", start=start - timedelta(days=10), end=end)
        feat_df = attach_labels(feat_df, close_panel)

    print(f"  after labels: rows={len(feat_df)}")
    print(f"  columns: {list(feat_df.columns)[:10]} ...")

    cols = [c for c in FEATURES if c in feat_df.columns]
    print(f"  active features: {len(cols)}")

    for target in ["ret_fwd_21d", "rank_fwd_21d"]:
        if target not in feat_df.columns:
            continue
        print(f"\n  -- target: {target} --")
        res = train_one(
            feat_df, feature_cols=cols, target_col=target,
            cluster_id="pilot", n_splits=4, embargo_days=21,
        )
        if res is None:
            print(f"    train_one returned None (likely <200 rows post-dropna)")
            continue
        print(f"    n={res.n_samples} R²_oof={res.final_r2_oof:+.4f} "
              f"hit={res.final_hit_rate_oof:.3f} IC={res.final_ic_oof:+.4f} "
              f"RMSE={res.final_rmse_oof:.4f}")
        top = sorted(res.feature_importance.items(), key=lambda kv: -kv[1])[:10]
        for k, v in top:
            print(f"      {k:24s} gain={v:.1f}")


if __name__ == "__main__":
    main()
