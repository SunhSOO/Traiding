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
]

FEATURE_COLS_REGIME = ["regime_risk_on", "regime_risk_off", "regime_conf"]

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

ALL_FEATURE_COLS = (
    FEATURE_COLS_PRICE + FEATURE_COLS_FUND + FEATURE_COLS_INFO
    + FEATURE_COLS_DISC + FEATURE_COLS_INSIDER
    + FEATURE_COLS_MACRO + FEATURE_COLS_REGIME
    + FEATURE_COLS_CALENDAR + FEATURE_COLS_CROSS_ASSET
)


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
