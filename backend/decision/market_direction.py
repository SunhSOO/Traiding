"""Market-direction model — predicts the forward 21d market (universe-mean)
return from standard timing signals (trend + momentum + VIX + credit spread +
DXY + regime-classifier output), aggregated to one market-level vector per date.

Used to GATE the integrated runner's concentrate mode: concentrate the exposure
into the timed passers only when the market direction is predicted up (and
confidently so). Backtest (2026-07-02, `--direction-gated`): this gate lifts US
Sharpe 0.62→0.72 (beats every fixed policy); it does NOT work for KR (forward
direction not predictable enough there), so enable per market, not blindly.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# macro fields are constant across tickers on a date → mean == the value
_MACRO = ["vix", "vix_5d_chg", "vix_21d_chg", "vix_pctile_252d", "ix_vix_mom",
          "hy_credit_spread", "hy_credit_5d_chg", "hy_credit_21d_chg",
          "dxy_5d_chg", "dxy_21d_chg", "usdkrw_21d_chg",
          "regime_risk_on", "regime_risk_off", "regime_neutral", "regime_calm_bull", "regime_conf"]
_BREADTH = ["px_vs_sma200", "px_vs_sma50", "sma50_above_sma200", "macd_above",
            "supertrend_dir", "stl_trend_strength"]
_MOM = ["ret_21d", "ret_63d", "ret_126d", "ret_252d"]
_RET = "ret_fwd_21d"


def build_market_frame(df: pd.DataFrame) -> pd.DataFrame:
    """One market-level feature vector per date (+ realized forward return)."""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    rows = []
    for d, g in df.groupby("date"):
        r = {"date": d, "fwd": pd.to_numeric(g.get(_RET), errors="coerce").mean()
             if _RET in g.columns else np.nan}
        for c in _MACRO:
            if c in g.columns:
                r[c] = pd.to_numeric(g[c], errors="coerce").mean()
        for c in _BREADTH:
            if c in g.columns:
                v = pd.to_numeric(g[c], errors="coerce")
                r[f"{c}_frac"] = float((v > 0).mean())
                r[f"{c}_mean"] = float(v.mean())
        for c in _MOM:
            if c in g.columns:
                r[f"{c}_mkt"] = pd.to_numeric(g[c], errors="coerce").mean()
        rows.append(r)
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def _params() -> dict:
    return dict(n_estimators=200, num_leaves=15, learning_rate=0.03,
                min_child_samples=20, subsample=0.8, colsample_bytree=0.7,
                reg_lambda=5.0, verbose=-1)


def predict_direction(feat_df: pd.DataFrame, *, embargo: int = 21,
                      min_train: int = 200, mode: str = "clf") -> Optional[float]:
    """Predicted forward-direction score for the LATEST date in ``feat_df``.

    Trains on all market-frame rows whose forward label is realised (≤ latest −
    embargo) — point-in-time safe. Returns None if there isn't enough history.
    A positive value means "market predicted up"; the runner concentrates only
    when this clears a confidence threshold.

    ``mode``: "clf" (classification P(up)−0.5, backtest-preferred: US OOS acc
    68% vs 63% baseline, beats "reg" 58%) or "reg" (predicted forward return).
    """
    import lightgbm as lgb

    mf = build_market_frame(feat_df)
    feats = [c for c in mf.columns if c not in ("date", "fwd")]
    if len(mf) < min_train + embargo or not feats:
        return None
    latest = mf.iloc[[-1]][feats].astype(float)
    train = mf.iloc[: len(mf) - embargo].dropna(subset=["fwd"])
    if len(train) < min_train:
        return None
    if mode == "clf":
        y = (train["fwd"] > 0).astype(int)
        if y.nunique() < 2:
            return None
        m = lgb.LGBMClassifier(**_params()).fit(train[feats].astype(float), y)
        return float(m.predict_proba(latest)[0, 1] - 0.5)
    m = lgb.LGBMRegressor(**_params()).fit(train[feats].astype(float), train["fwd"].astype(float))
    return float(m.predict(latest)[0])
