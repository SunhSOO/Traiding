"""Wave 2D — Cross-sectional rank + Interaction + Lag features.

This is applied AFTER all per-ticker features are computed and joined
into the panel DataFrame. Operates at the (date, market, ticker) panel
level rather than per-ticker.

Output:
  - {feat}_xrank : per-date cross-sectional percentile (0-1)
                    for top ~30 high-signal features
  - {feat}_xrank_sector : same but within sector (needs sector join)
  - {feat}_lag_5 / _lag_21 : lagged copies
  - Interaction features (16): rsi×vol, piotroski×momentum, etc.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# Features that benefit most from cross-sectional rank normalization
XRANK_FEATURES = [
    # Valuation
    "pe_ttm", "ps", "pb", "ev_ebitda", "pfcf", "ev_sales", "ev_fcf",
    "earnings_yield", "fcf_yield", "dividend_yield", "buyback_yield",
    "p_tangible_bv", "shiller_pe_ttm",
    # Quality
    "roe_q", "roic", "roce", "op_margin", "net_margin", "ebitda_margin",
    "asset_turnover", "ccc_days", "earnings_quality", "rnd_intensity",
    # Growth
    "rev_3y_cagr", "eps_3y_cagr", "fcf_3y_cagr", "sgr",
    # Composites
    "piotroski_score", "altman_z", "magic_formula_score",
    "qmj_score", "mohanram_g_score",
    # Momentum
    "ret_21d", "ret_63d", "ret_252d", "sharpe_63d",
]

# Interaction features: (left, right, name, kind)
INTERACTIONS: list[tuple[str, str, str, str]] = [
    # Quality × Momentum (Asness style)
    ("piotroski_score", "ret_63d", "ix_pio_mom", "mul"),
    ("magic_formula_score", "ret_63d", "ix_mf_mom", "mul"),
    ("qmj_score", "ret_63d", "ix_qmj_mom", "mul"),
    # Value × Momentum
    ("earnings_yield", "ret_63d", "ix_ey_mom", "mul"),
    ("fcf_yield", "ret_63d", "ix_fy_mom", "mul"),
    # Trend × Volume
    ("rsi14", "volume_z21", "ix_rsi_volz", "mul"),
    ("macd_hist", "volume_z21", "ix_macd_volz", "mul"),
    ("adx14", "volume_z21", "ix_adx_volz", "mul"),
    # Volatility × Sentiment
    ("vol_21d", "news_sentiment_7d", "ix_vol_news", "mul"),
    ("vol_21d", "gdelt_tone_momentum", "ix_vol_gdelt", "mul"),
    # Mean reversion combos
    ("rsi14", "bb_pctb", "ix_rsi_bb", "mul"),
    ("bb_pctb", "vol_21d", "ix_bb_vol", "mul"),
    # Information × Insider
    ("news_count_7d", "insider_buys_30d", "ix_news_buys", "mul"),
    ("news_sentiment_7d", "insider_buy_sell_ratio_30d", "ix_sent_isr", "mul"),
    # Macro × Stock
    ("vix", "ret_21d", "ix_vix_mom", "mul"),
    ("us10y", "pe_ttm", "ix_yield_pe", "mul"),
]

LAG_FEATURES = [
    "ret_5d", "ret_21d", "ret_63d", "rsi14", "macd_hist",
    "bb_pctb", "vol_21d", "volume_z21",
    "news_sentiment_7d", "gdelt_tone_momentum",
    "piotroski_score", "earnings_yield", "fcf_yield",
]


def compute_cross_sectional_ranks(panel: pd.DataFrame) -> pd.DataFrame:
    """Add {feat}_xrank columns: per-date percentile rank in 0..1."""
    if panel.empty:
        return panel
    available = [c for c in XRANK_FEATURES if c in panel.columns]
    if not available:
        return panel
    out = panel.copy()
    for col in available:
        new_col = f"{col}_xrank"
        out[new_col] = panel.groupby("date")[col].rank(pct=True, method="average")
    return out


def compute_interactions(panel: pd.DataFrame) -> pd.DataFrame:
    """Add interaction features."""
    if panel.empty:
        return panel
    out = panel.copy()
    for left, right, name, kind in INTERACTIONS:
        if left not in panel.columns or right not in panel.columns:
            continue
        l = panel[left].astype(float)
        r = panel[right].astype(float)
        if kind == "mul":
            out[name] = l * r
        elif kind == "div":
            out[name] = l / r.replace(0, np.nan)
        elif kind == "sub":
            out[name] = l - r
    return out


def compute_lags(panel: pd.DataFrame) -> pd.DataFrame:
    """Add {feat}_lag_5 / _lag_21 columns. Group by (market, ticker)."""
    if panel.empty:
        return panel
    available = [c for c in LAG_FEATURES if c in panel.columns]
    if not available:
        return panel
    out = panel.copy()
    panel_sorted = panel.sort_values(["market", "ticker", "date"])
    for col in available:
        out[f"{col}_lag5"] = panel_sorted.groupby(["market", "ticker"])[col].shift(5).reindex(panel.index)
        out[f"{col}_lag21"] = panel_sorted.groupby(["market", "ticker"])[col].shift(21).reindex(panel.index)
    return out


def apply_cross_section_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Apply all cross-section transformations."""
    panel = compute_cross_sectional_ranks(panel)
    panel = compute_interactions(panel)
    panel = compute_lags(panel)
    return panel


# Column name lists for train_lgbm registration
def get_xrank_cols() -> list[str]:
    return [f"{c}_xrank" for c in XRANK_FEATURES]


def get_interaction_cols() -> list[str]:
    return [name for _, _, name, _ in INTERACTIONS]


def get_lag_cols() -> list[str]:
    return [f"{c}_lag5" for c in LAG_FEATURES] + [f"{c}_lag21" for c in LAG_FEATURES]
