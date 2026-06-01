"""Debug feature matrix builder — print why rows are dropping."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.db import session_scope
from sqlalchemy import select
from core.models.prices import DailyPrice
from training.features import (
    build_feature_matrix, compute_price_features,
    compute_info_features, load_macro_features, load_regime_features,
)
from training.labels_multi import attach_labels, load_close_panel


def main() -> None:
    end = date.today()
    start = end - timedelta(days=90)

    with session_scope() as s:
        # Direct query
        rows = list(s.execute(
            select(DailyPrice.trade_date, DailyPrice.close)
            .where(DailyPrice.market == "US", DailyPrice.ticker == "AAPL",
                   DailyPrice.trade_date >= start - timedelta(days=300),
                   DailyPrice.trade_date <= end)
            .order_by(DailyPrice.trade_date)
        ).all())
        print(f"direct SQL: AAPL rows in [{start - timedelta(days=300)}, {end}] = {len(rows)}")
        if rows:
            print(f"  first: {rows[0][0]} last: {rows[-1][0]}")

        feat_df, rep = build_feature_matrix(
            s, market="US", start=start, end=end, tickers=["AAPL"],
        )
        print(f"build_feature_matrix(['AAPL']):")
        print(f"  rep: rows={rep.rows_emitted} cols={rep.feature_columns}")
        print(f"  df shape: {feat_df.shape}")
        if not feat_df.empty:
            print(f"  date range: {feat_df['date'].min()} to {feat_df['date'].max()}")
            print(f"  unique dates: {feat_df['date'].nunique()}")
            print(f"  NaN-per-col (first 15 features):")
            print(feat_df.iloc[:, 3:18].isna().sum().to_string())

        close_panel = load_close_panel(s, market="US",
                                       start=start - timedelta(days=10), end=end)
        feat_df = attach_labels(feat_df, close_panel)
        print(f"\nAfter attach_labels: rows={len(feat_df)}")
        if not feat_df.empty:
            print(f"  ret_fwd_21d NaN: {feat_df['ret_fwd_21d'].isna().sum()}/{len(feat_df)}")
            print(f"  rank_fwd_21d NaN: {feat_df['rank_fwd_21d'].isna().sum()}/{len(feat_df)}")


if __name__ == "__main__":
    main()
