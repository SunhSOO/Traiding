"""Multi-horizon labels — raw forward returns + cross-sectional rank.

Given a feature DataFrame indexed by (date, market, ticker), compute:

* ``ret_fwd_5d``, ``ret_fwd_21d``, ``ret_fwd_63d`` — raw forward returns.
* ``rank_fwd_5d``, ``rank_fwd_21d`` — cross-sectional rank within
  market on each date (percentile in [0, 1]).
* ``vol_adj_ret_21d`` — forward 21d return divided by trailing 21d
  realised vol (risk-adjusted target).

Cross-sectional rank is the recommended primary target — it removes
market-wide moves so the model learns *relative* outperformance, not
absolute returns. This is what production quant systems use.
"""
from __future__ import annotations

from datetime import date as DateType, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.prices import DailyPrice


def load_close_panel(
    session: Session, *, market: str,
    start: DateType, end: DateType,
) -> pd.DataFrame:
    """Return wide DataFrame indexed by trade_date with one column per ticker."""
    stmt = (
        select(DailyPrice.ticker, DailyPrice.trade_date, DailyPrice.close)
        .where(
            DailyPrice.market == market,
            DailyPrice.trade_date >= start,
            DailyPrice.trade_date <= end,
        )
    )
    rows = list(session.execute(stmt).all())
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ticker", "trade_date", "close"])
    df["close"] = df["close"].astype(float)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.pivot_table(index="trade_date", columns="ticker", values="close", aggfunc="last")


def attach_labels(
    feat_df: pd.DataFrame, close_panel: pd.DataFrame,
    *, horizons: tuple[int, ...] = (5, 21, 63),
) -> pd.DataFrame:
    """Add forward-return + rank columns to feat_df.

    feat_df: columns include ['date', 'market', 'ticker', ...features...]
    close_panel: wide [date × ticker] close-price panel.
    """
    if feat_df.empty:
        return feat_df

    df = feat_df.copy()
    df["date"] = pd.to_datetime(df["date"])

    # Build forward-return panels once per horizon
    fwd_panels: dict[int, pd.DataFrame] = {}
    for h in horizons:
        fwd_panels[h] = close_panel.pct_change(h).shift(-h)

    # Realised vol (trailing 21d std of daily returns) for risk-adjusted target
    daily_ret = close_panel.pct_change(1)
    vol21_panel = daily_ret.rolling(21).std()

    for h in horizons:
        col = f"ret_fwd_{h}d"
        df[col] = [
            float(fwd_panels[h].at[d, t]) if d in fwd_panels[h].index and t in fwd_panels[h].columns else np.nan
            for d, t in zip(df["date"], df["ticker"])
        ]

    # Cross-sectional rank for 5d and 21d (most useful)
    for h in (5, 21):
        col_raw = f"ret_fwd_{h}d"
        col_rank = f"rank_fwd_{h}d"
        df[col_rank] = df.groupby("date")[col_raw].rank(pct=True)

    # Risk-adjusted (21d return / 21d vol, computed as-of row date)
    vols = []
    for d, t in zip(df["date"], df["ticker"]):
        try:
            v = float(vol21_panel.at[d, t])
        except (KeyError, ValueError):
            v = np.nan
        vols.append(v)
    df["realised_vol_21d"] = vols
    df["vol_adj_ret_21d"] = df["ret_fwd_21d"] / df["realised_vol_21d"].replace(0, np.nan)

    return df
