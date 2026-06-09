"""HMM-based regime detection — Wave 1 (replaces 3-state voter).

Uses Gaussian HMM (hmmlearn) on macro feature vector to detect 5 latent
regimes:
  0. Calm Bull (low VIX, positive SP500 momentum, contango yield curve)
  1. Risk-On Rally
  2. Neutral / Transition
  3. Risk-Off / Correction
  4. Crisis (high VIX, inverted yield curve, USD spike)

Inputs (built per date):
  - VIX level
  - VIX 5d change
  - SP500 21d return
  - DXY 5d change
  - US10Y - US2Y yield curve
  - FedFunds

Output: regime label + posterior probability of each state.

Falls back to features.py 3-state classifier if HMM training data is
insufficient (<252 observations).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.prices import MacroSeries


REGIME_LABELS = ["calm_bull", "risk_on", "neutral", "risk_off", "crisis"]


@dataclass
class HMMRegimeResult:
    label: str
    state_idx: int
    posterior: np.ndarray   # shape (n_states,)
    confidence: float       # max posterior


def _load_macro_inputs(session: Session) -> pd.DataFrame:
    codes = ["VIX", "FX_DXY", "IDX_SP500_FRED",
             "RATE_US_10Y", "RATE_US_2Y", "FEDFUNDS_US"]
    rows = list(session.execute(
        select(MacroSeries.series_code, MacroSeries.ts, MacroSeries.value)
        .where(MacroSeries.series_code.in_(codes))
        .order_by(MacroSeries.ts)
    ).all())
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["series_code", "ts", "value"])
    df["value"] = df["value"].astype(float)
    df["ts"] = pd.to_datetime(df["ts"])
    panel = df.pivot_table(index="ts", columns="series_code", values="value", aggfunc="first")
    panel = panel.ffill()
    feat = pd.DataFrame(index=panel.index)
    if "VIX" in panel:
        feat["vix"] = panel["VIX"]
        feat["vix_5d_chg"] = panel["VIX"].pct_change(5)
    if "IDX_SP500_FRED" in panel:
        feat["sp500_21d_ret"] = panel["IDX_SP500_FRED"].pct_change(21)
    if "FX_DXY" in panel:
        feat["dxy_5d_chg"] = panel["FX_DXY"].pct_change(5)
    if "RATE_US_10Y" in panel and "RATE_US_2Y" in panel:
        feat["yield_curve_2_10"] = panel["RATE_US_10Y"] - panel["RATE_US_2Y"]
    if "FEDFUNDS_US" in panel:
        feat["fedfunds"] = panel["FEDFUNDS_US"]
    return feat.dropna()


def train_and_predict(
    session: Session, n_states: int = 5, n_iter: int = 100,
) -> Optional[pd.DataFrame]:
    """Fit Gaussian HMM on full history; return per-date state + posterior."""
    try:
        from hmmlearn import hmm
    except ImportError:
        print("[hmm] hmmlearn not installed; skip")
        return None

    X_df = _load_macro_inputs(session)
    if len(X_df) < 252:
        print(f"[hmm] insufficient macro history ({len(X_df)}); skip")
        return None

    X = X_df.values
    # Standardise (HMM Gaussian sensitive to scale)
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    Z = (X - means) / np.where(stds == 0, 1.0, stds)

    model = hmm.GaussianHMM(
        n_components=n_states, covariance_type="full", n_iter=n_iter,
        random_state=42, tol=1e-3,
    )
    model.fit(Z)
    states = model.predict(Z)
    posteriors = model.predict_proba(Z)

    # Label states by VIX level (state with lowest avg VIX = calm_bull)
    state_vix_mean = {
        s: X[states == s, 0].mean() if (states == s).sum() > 0 else 0.0
        for s in range(n_states)
    }
    sorted_states = sorted(state_vix_mean, key=lambda s: state_vix_mean[s])
    state_to_label = {s: REGIME_LABELS[i] for i, s in enumerate(sorted_states)}

    out = pd.DataFrame(index=X_df.index)
    out["hmm_state_idx"] = states
    out["hmm_label"] = [state_to_label[s] for s in states]
    out["hmm_confidence"] = posteriors.max(axis=1)
    for i in range(n_states):
        out[f"hmm_post_{state_to_label[sorted_states[i]]}"] = posteriors[:, sorted_states[i]]
    return out


def predict_for_date(
    session: Session, as_of: DateType, n_states: int = 5,
) -> Optional[HMMRegimeResult]:
    df = train_and_predict(session, n_states=n_states)
    if df is None or df.empty:
        return None
    df.index = pd.to_datetime(df.index)
    as_of_ts = pd.Timestamp(as_of)
    eligible = df[df.index <= as_of_ts]
    if eligible.empty:
        return None
    row = eligible.iloc[-1]
    state_idx = int(row["hmm_state_idx"])
    label = row["hmm_label"]
    cols = [c for c in df.columns if c.startswith("hmm_post_")]
    posterior = row[cols].values.astype(float)
    return HMMRegimeResult(
        label=label, state_idx=state_idx,
        posterior=posterior, confidence=float(row["hmm_confidence"]),
    )
