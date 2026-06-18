"""End-to-end LightGBM training pipeline.

Steps:
1. Pull feature matrix via training.features.build_feature_matrix
2. Attach multi-horizon labels via training.labels_multi.attach_labels
3. Join with ticker_clusters
4. For each target (raw 21d return, cross-sectional rank 21d), train
   per-cluster + global LightGBM models with time-series CV
5. Print summary table + write summary JSON

Usage:

    uv run python scripts/train_lgbm.py [--market US] [--days 365]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models.training import TickerClusterAssignment as TickerCluster
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel
from training.lgbm_trainer import train_per_cluster_and_global


FEATURE_COLS_PRICE = [
    # Multi-horizon returns
    "ret_1d", "ret_2d", "ret_3d", "ret_5d", "ret_10d",
    "ret_21d", "ret_42d", "ret_63d", "ret_126d", "ret_252d",
    # Volatility + return distribution
    "vol_5d", "vol_21d", "vol_63d", "vol_252d",
    "ret_skew_21d", "ret_kurt_21d",
    "sharpe_21d", "sharpe_63d",
    # Trend / drawdown
    "px_vs_sma20", "px_vs_sma50", "px_vs_sma200",
    "sma50_above_sma200",
    "dd_from_high_63d", "dd_from_high_252d",
    # Range + volume
    "range_pct", "range_pct_5d_avg",
    "volume_z21", "volume_z63", "gap_pct",
    # Indicators
    "rsi14", "rsi5",
    "macd_hist", "macd_above",
    "bb_pctb", "bb_squeeze",
    "adx14", "stoch_k14", "williams_r14",
    "mfi14", "cmf21",
    "atr_pct", "obv_slope21",
    "ulcer14",
    "donchian_pos_20", "donchian_pos_55",
    "aroon_up", "aroon_dn", "aroon_osc",
    "roc_10", "roc_21",
    # Candle pattern bits
    "candle_body_pct", "candle_upper_wick_pct", "candle_lower_wick_pct",
    "is_doji",
]

FEATURE_COLS_FUND = [
    "pe_ttm", "pb", "ev_ebitda", "roe_q", "roa_q",
    "debt_equity", "current_ratio", "gross_margin",
    "rev_yoy", "eps_yoy",
]

FEATURE_COLS_INFO = [
    "news_count_7d", "news_count_30d",
    "news_sentiment_7d", "news_pos_count_7d",
    "news_neg_count_7d", "news_impact_7d",
]

FEATURE_COLS_DISC = [
    "insider_count_7d", "insider_count_30d",
    "event_8k_count_7d", "event_8k_count_30d",
    "days_since_last_10k", "days_since_last_10q",
]

FEATURE_COLS_INSIDER = [
    "insider_net_value_30d", "insider_net_value_7d",
    "insider_buys_30d", "insider_sells_30d",
    "insider_ceo_buys_30d", "insider_director_buys_30d",
    "insider_buy_sell_ratio_30d",
]

FEATURE_COLS_MACRO = [
    "vix", "vix_5d_chg", "vix_21d_chg",
    "dxy_5d_chg", "dxy_21d_chg",
    "sp500_21d_ret", "sp500_63d_ret",
    "us10y", "us10y_5d_chg", "us2y", "yield_curve_2_10",
    "fedfunds", "cpi_us_yoy", "m2_us_yoy",
    "unrate_us", "unrate_us_chg",
    "kr_base_rate", "cpi_kr_yoy",
    # Wave 4 — macro/cross-asset depth
    "real_yield_10y", "real_yield_21d_chg",
    "breakeven_10y", "breakeven_21d_chg",
    "hy_credit_spread", "hy_credit_5d_chg", "hy_credit_21d_chg",
    "copper_63d_ret", "wti_21d_ret", "natgas_21d_ret",
    "yield_curve_5_30", "yield_curvature",
    "usdkrw_21d_chg", "usdjpy_21d_chg",
    "vix_pctile_252d", "vol_risk_premium", "funding_stress",
]

FEATURE_COLS_REGIME = [
    "regime_risk_on", "regime_risk_off", "regime_conf",
    "regime_calm_bull", "regime_neutral", "regime_crisis",
]

FEATURE_COLS_CALENDAR = [
    "dow", "dom", "doq", "doy", "month", "quarter",
    "days_to_q_end", "is_jan", "is_dec",
]

FEATURE_COLS_CROSS_ASSET = [
    "rel_xlk_21d", "rel_xlf_21d", "rel_xlv_21d", "rel_xle_21d",
    "rel_xly_21d", "rel_xlp_21d", "rel_xli_21d", "rel_xlb_21d",
    "rel_xlu_21d", "rel_xlre_21d", "rel_xlc_21d",
    "rel_spy_21d", "rel_qqq_21d", "rel_iwm_21d",
    "rel_gld_21d", "rel_uso_21d", "rel_tlt_21d",
    "corr_spy_63d",
]

FEATURE_COLS_EXTRA_TECH = [
    # extra technical from features_advanced.py
    "trix_15", "dpo_20", "tsi", "ppo", "pvo", "bop", "chande_20",
    "vortex_plus", "vortex_minus", "vortex_spread",
    "ult_osc", "coppock",
    "px_vs_kama", "px_vs_hma20", "supertrend_dir",
    "vol_gk_21d", "vol_gk_63d", "vol_yz_21d", "vol_yz_63d",
    "bb_width_5d_chg",
    "volume_z5d", "volume_z252d",
    "sma50_sma200_dist", "sma50_sma200_5d_chg",
    # candle patterns
    "pat_hammer", "pat_shooting_star",
    "pat_bull_engulf", "pat_bear_engulf",
    "pat_doji", "pat_marubozu", "pat_spinning_top",
    "pat_3_white_soldiers", "pat_3_black_crows", "pat_inside_day",
]

FEATURE_COLS_STAT = [
    "corr_mkt_21d", "corr_mkt_63d", "corr_mkt_252d",
    "beta_21d", "beta_63d", "beta_252d",
    "alpha_63d",
    "resid_mom_blitz_12m", "resid_mom_blitz_6m",
    "tracking_err_63d", "info_ratio_63d", "sortino_63d",
    "ret_skew_63d", "ret_kurt_63d", "ret_skew_252d",
    "autocorr_1", "autocorr_5", "autocorr_21",
    "hurst_100",
]

FEATURE_COLS_MICRO = [
    "close_range_strength", "opening_gap", "abs_opening_gap",
    "range_atr_ratio", "effective_spread_proxy",
    "dollar_volume", "dollar_volume_z21", "dollar_volume_z63",
    "distinct_closes_21d",
]

FEATURE_COLS_FS_COMPOSITE = [
    "piotroski_score", "altman_z", "beneish_m",
]

FEATURE_COLS_EVENT_CAL = [
    "is_fomc_day", "days_to_fomc", "days_since_fomc",
    "is_bok_day", "days_to_bok",
    "is_nfp_day", "days_to_nfp",
    "is_cpi_day", "days_to_cpi",
    "is_pce_day", "is_gdp_day", "is_earnings_season",
    "days_to_quarter_end", "days_to_year_end",
]

FEATURE_COLS_GDELT = [
    "gdelt_tone_avg_7d", "gdelt_tone_avg_30d", "gdelt_tone_std_30d",
    "gdelt_tone_momentum", "gdelt_pos_count_7d", "gdelt_neg_count_7d",
    "gdelt_mention_count_7d",
]

# Wave 2 — Fundamental v2 (Valuation/Quality/Growth/Leverage/CF/Composites)
FEATURE_COLS_FUND_V2 = [
    # Valuation 12
    "peg", "ps", "pfcf", "ev_sales", "ev_fcf", "ev_ebit",
    "earnings_yield", "dividend_yield", "fcf_yield",
    "shiller_pe_ttm", "p_tangible_bv", "buyback_yield",
    # Quality 12
    "roic", "roce", "op_margin", "net_margin", "ebitda_margin",
    "asset_turnover", "inv_turnover", "recv_turnover",
    "ccc_days", "earnings_quality", "accruals_ratio", "rnd_intensity",
    # Growth 10
    "rev_3y_cagr", "rev_5y_cagr", "eps_3y_cagr", "eps_5y_cagr",
    "bv_3y_cagr", "fcf_3y_cagr", "div_3y_cagr",
    "rev_qoq", "rev_accel", "sgr",
    # Leverage 8
    "net_debt_ebitda", "interest_coverage", "quick_ratio",
    "cash_total_debt", "lt_debt_capital", "fcf_total_debt",
    "goodwill_assets", "intangibles_assets",
    # Cash Flow 7
    "fcf_abs_log", "fcf_margin", "capex_sales", "capex_dep",
    "delta_wc_assets", "cash_conv", "owner_earnings_yield",
    # Composites 6
    "magic_formula_score", "qmj_score", "ohlson_o",
    "sloan_accruals_signal", "mohanram_g_score", "ncav_to_mcap",
]

# Wave 2 — Technical v2 (Ichimoku/Divergence/TTM/Pivot/OrderFlow/Accel/SR)
FEATURE_COLS_TECH_V2 = [
    # Ichimoku 8
    "ichimoku_tenkan_dist", "ichimoku_kijun_dist",
    "ichimoku_senkou_a_dist", "ichimoku_senkou_b_dist",
    "ichimoku_chikou_dist", "ichimoku_cloud_thick",
    "ichimoku_px_vs_kumo", "ichimoku_kumo_twist",
    # Divergence 4
    "rsi_divergence", "macd_divergence", "obv_divergence", "hidden_divergence",
    # TTM Squeeze 3
    "ttm_squeeze_on", "ttm_squeeze_duration", "ttm_squeeze_fire",
    # Pivot 6
    "pivot_std_dist", "pivot_r1_dist", "pivot_s1_dist",
    "pivot_fib_r1_dist", "pivot_cam_h3_dist", "pivot_cam_l3_dist",
    # Order Flow 5
    "amihud_illiquidity", "kyle_lambda", "roll_spread",
    "uptick_volume_ratio", "vpoc_dist_proxy",
    # Indicator accel 5
    "rsi_5d_chg", "macd_hist_5d_chg", "adx_5d_chg",
    "bb_pctb_5d_chg", "volume_z_5d_chg",
    # S/R 4
    "dist_to_52w_high", "dist_to_52w_low",
    "round_number_dist", "tests_at_resistance_21d",
]

# Wave 2 — Information v2 (news/insider/SEC text)
FEATURE_COLS_INFO_V2 = [
    # News v2 8
    "news_velocity_7d", "news_spike_z_30d", "news_source_q_sent_7d",
    "news_headline_body_div_7d", "news_topic_ma_7d", "news_topic_legal_30d",
    "news_topic_earnings_7d", "news_topic_product_30d",
    # Insider v2 6
    "insider_cluster_buy_30d", "insider_ceo_cfo_cobuy_30d",
    "insider_openmarket_ratio", "insider_avg_cost_dist",
    "insider_exec_buy_weight_30d", "insider_dir_buy_weight_30d",
    # SEC text 5 (NaN until edgar_10k_lm_sentiment.py runs)
    "lm_sent_10k", "risk_factor_chg_pct", "fog_index_10k",
    "going_concern_count_10k", "restatement_flag",
]

from training.features_cross_section import (
    get_xrank_cols, get_interaction_cols, get_lag_cols,
    apply_cross_section_features,
)
FEATURE_COLS_XRANK = get_xrank_cols()
FEATURE_COLS_INTERACTION = get_interaction_cols()
FEATURE_COLS_LAG = get_lag_cols()

# Wave 3 — FinBERT sentiment aggregators
FEATURE_COLS_FINBERT = [
    "finbert_pos_avg_7d", "finbert_pos_avg_30d",
    "finbert_neg_avg_7d", "finbert_neg_avg_30d",
    "finbert_net_sent_7d", "finbert_net_sent_30d",
    "finbert_sent_vol_30d", "finbert_strong_pos_30d", "finbert_strong_neg_30d",
    "finbert_sent_momentum", "finbert_label_pos_ratio_7d", "finbert_n_articles_7d",
]

# Wave 3 — Wavelet/STL embeddings
FEATURE_COLS_EMB = [
    "wavelet_e0", "wavelet_e1", "wavelet_e2", "wavelet_e3", "wavelet_e4", "wavelet_e5",
    "wavelet_hf_ratio",
    "stl_trend_strength", "stl_seasonal_strength", "stl_resid_ratio",
]

# Wave 2E — Alt data (Short/Options/Wiki/Trends/Reddit/Patents/13F/GCAM)
FEATURE_COLS_ALT_DATA = [
    "short_ratio", "short_ratio_5d", "short_ratio_z30",
    "short_ratio_chg_7d", "short_squeeze_score",
    "pc_vol_ratio", "pc_oi_ratio", "iv_atm", "iv_skew",
    "iv_term_slope", "unusual_count",
    "wiki_views_7d", "wiki_views_z_30d", "wiki_views_chg_7d",
    "trends_interest_7d", "trends_z_30d", "trends_chg_7d",
    "reddit_mentions_7d", "reddit_score_avg_7d", "reddit_z_30d",
    "patents_count_90d", "patents_avg_cites_90d", "patents_chg_yoy",
    "inst_filing_count_q", "inst_filing_count_q_chg",
    "gcam_fear_7d", "gcam_anger_7d", "gcam_econ_neg_7d", "gcam_polarity_30d",
]

# Wave 3 — Duplicate features (corr=1.0 in EDA), drop to avoid multicollinearity
_DUPLICATE_FEATURES = {
    "roc_10", "roc_21",                          # == ret_10d / ret_21d
    "williams_r14",                              # == stoch_k14
    "hidden_divergence",                          # == rsi_divergence
    "opening_gap",                                # == gap_pct
    "cash_conv",                                  # == earnings_quality
    "sloan_accruals_signal",                      # == -accruals_ratio
    "gdelt_mention_count_7d", "finbert_n_articles_7d",
    "news_source_q_sent_7d",                      # all == news_count_7d
    "days_to_year_end",                           # == doy
}

_RAW_ALL = (
    FEATURE_COLS_PRICE + FEATURE_COLS_FUND + FEATURE_COLS_INFO
    + FEATURE_COLS_DISC + FEATURE_COLS_INSIDER
    + FEATURE_COLS_MACRO + FEATURE_COLS_REGIME
    + FEATURE_COLS_CALENDAR + FEATURE_COLS_CROSS_ASSET
    + FEATURE_COLS_EXTRA_TECH + FEATURE_COLS_STAT
    + FEATURE_COLS_MICRO + FEATURE_COLS_FS_COMPOSITE
    + FEATURE_COLS_EVENT_CAL + FEATURE_COLS_GDELT
    + FEATURE_COLS_FUND_V2 + FEATURE_COLS_TECH_V2 + FEATURE_COLS_INFO_V2
    + FEATURE_COLS_XRANK + FEATURE_COLS_INTERACTION + FEATURE_COLS_LAG
    + FEATURE_COLS_ALT_DATA + FEATURE_COLS_EMB + FEATURE_COLS_FINBERT
)
ALL_FEATURE_COLS = [c for c in _RAW_ALL if c not in _DUPLICATE_FEATURES]


def attach_clusters(df: pd.DataFrame, session) -> pd.DataFrame:
    rows = list(session.execute(
        select(TickerCluster.market, TickerCluster.ticker, TickerCluster.cluster_id)
    ).all())
    if not rows:
        df["cluster_id"] = "__none__"
        return df
    cluster_df = pd.DataFrame(rows, columns=["market", "ticker", "cluster_id"])
    return df.merge(cluster_df, on=["market", "ticker"], how="left")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["KR", "US"])
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--targets", default="ret_fwd_21d,rank_fwd_21d",
                    help="comma-separated target columns")
    ap.add_argument("--out", default="var/lgbm_summary.json")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    print(f"[lgbm] market={args.market} window={start}..{end} days={args.days}")

    with session_scope() as s:
        feat_df, fb_rep = build_feature_matrix(
            s, market=args.market, start=start, end=end,
        )
        if feat_df.empty:
            print("  no features built; abort")
            sys.exit(1)
        print(f"  feature matrix: rows={fb_rep.rows_emitted} "
              f"cols={fb_rep.feature_columns} tickers={fb_rep.tickers_processed}")

        close_panel = load_close_panel(s, market=args.market, start=start - timedelta(days=10), end=end)
        feat_df = attach_labels(feat_df, close_panel)
        feat_df = attach_clusters(feat_df, s)

    # Wave 2D: apply cross-sectional rank + interactions + lags
    # Done outside session because it's pure pandas
    feat_df = apply_cross_section_features(feat_df)

    # Optionally restrict to active feature cols that actually exist
    cols_avail = [c for c in ALL_FEATURE_COLS if c in feat_df.columns]
    print(f"  active features: {len(cols_avail)}/{len(ALL_FEATURE_COLS)}")

    targets = [t.strip() for t in args.targets.split(",")]
    summary = {"market": args.market, "start": str(start), "end": str(end),
               "n_rows": len(feat_df), "features": cols_avail, "results": {}}

    for target in targets:
        if target not in feat_df.columns:
            print(f"  [skip] target {target} not in DataFrame")
            continue
        print(f"\n  ---- target: {target} ----")
        results = train_per_cluster_and_global(
            feat_df, feature_cols=cols_avail, target_col=target,
            min_samples_per_cluster=400,
        )
        summary["results"][target] = {}
        for cid, res in sorted(results.items(), key=lambda kv: -kv[1].n_samples):
            line = (
                f"    {cid:30s} n={res.n_samples:>5d} "
                f"R²_oof={res.final_r2_oof:+.4f} "
                f"hit={res.final_hit_rate_oof:.3f} "
                f"IC={res.final_ic_oof:+.4f}"
            )
            print(line)
            summary["results"][target][cid] = res.as_metrics_jsonb()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n  summary -> {out_path}")

    # Persist LightGBM models per (target, cluster) for later inference.
    # Skip in this round (would require re-running the train loop with model
    # capture). The summary JSON above carries metrics + feature importance,
    # which is what we need for decision-time integration design.


if __name__ == "__main__":
    main()
