"""Raw feature extraction for the LightGBM trainer.

Joins price + financial_facts + news mentions + macro + regime per
(date, ticker) and emits a single feature DataFrame ready for fitting.

Design choices:
* Price features come from ``daily_prices`` directly; we recompute
  indicators here instead of reading ``module_scores.score`` so the
  model sees raw signals (RSI level, MACD histogram) instead of the
  aggregated summary — much more information per row.
* Fundamental features use rolling latest from ``financial_facts``
  for each (ticker, period_kind='Q' or 'A') as of the date. Stale-OK
  per ratio (financials evolve quarterly).
* News features count tagged mentions + average sentiment in trailing
  N days from ``news_ticker_mentions`` + ``article_classifications``.
* Cross-asset features come from ``macro_series``; regime from
  ``market_regime`` (one-hot).
* All features are computed *as of* the row date with strict
  ``as_of_ts <= row_date`` to avoid look-ahead.

Output schema: ``(date, market, ticker, feat_*)`` with ~40 columns.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType, datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from core.models.classifications import ArticleClassification
from core.models.financials import FinancialFact
from core.models.news import NewsArticle, NewsTickerMention
from core.models.prices import DailyPrice, MacroSeries
from core.models.regime import MarketRegime
from core.models.universe import Security


# ──────────────────────────────────────────────────────────────────────
# Price feature engineering
# ──────────────────────────────────────────────────────────────────────


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    avg_up = up.ewm(alpha=1 / period, adjust=False).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal = macd_line.ewm(span=sig, adjust=False).mean()
    hist = macd_line - signal
    return macd_line, signal, hist


def _bbands_percent(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.Series:
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std()
    upper = ma + k * sd
    lower = ma - k * sd
    return (close - lower) / (upper - lower)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    atr = _atr(high, low, close, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def _stoch_k(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    ll = low.rolling(period).min()
    hh = high.rolling(period).max()
    return 100 * (close - ll) / (hh - ll).replace(0, np.nan)


def _obv_slope(close: pd.Series, volume: pd.Series, period: int = 21) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    obv = (direction * volume).cumsum()
    # Slope = (obv_now - obv_period_ago) / period, normalised by avg volume
    obv_change = obv.diff(period)
    return obv_change / (volume.rolling(period).mean() * period).replace(0, np.nan)


def compute_price_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute ~20 price/volume features from one ticker's OHLCV bars.

    ``bars`` indexed by trade_date ascending with [open, high, low, close, volume]
    Returns DataFrame indexed by same dates with feature columns.
    """
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    volume = bars["volume"].astype(float)

    feat = pd.DataFrame(index=bars.index)
    # Returns
    feat["ret_1d"] = close.pct_change(1)
    feat["ret_5d"] = close.pct_change(5)
    feat["ret_21d"] = close.pct_change(21)
    feat["ret_63d"] = close.pct_change(63)
    # Volatility (realized)
    daily_ret = close.pct_change(1)
    feat["vol_21d"] = daily_ret.rolling(21).std() * np.sqrt(252)
    feat["vol_63d"] = daily_ret.rolling(63).std() * np.sqrt(252)
    # Price vs moving average
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    feat["px_vs_sma50"] = close / sma50 - 1.0
    feat["px_vs_sma200"] = close / sma200 - 1.0
    # Drawdown from rolling high
    feat["dd_from_high_63d"] = close / close.rolling(63).max() - 1.0
    # Daily range
    feat["range_pct"] = (high - low) / close
    # Volume z-score
    vol_ma = volume.rolling(21).mean()
    vol_sd = volume.rolling(21).std()
    feat["volume_z21"] = (volume - vol_ma) / vol_sd.replace(0, np.nan)
    # Technical indicators
    feat["rsi14"] = _rsi(close, 14)
    macd_line, macd_sig, macd_hist = _macd(close, 12, 26, 9)
    feat["macd_hist"] = macd_hist
    feat["macd_above"] = (macd_line > macd_sig).astype(float)
    feat["bb_pctb"] = _bbands_percent(close, 20, 2.0)
    feat["adx14"] = _adx(high, low, close, 14)
    feat["stoch_k14"] = _stoch_k(high, low, close, 14)
    atr = _atr(high, low, close, 14)
    feat["atr_pct"] = atr / close
    feat["obv_slope21"] = _obv_slope(close, volume, 21)

    return feat


# ──────────────────────────────────────────────────────────────────────
# Fundamental features
# ──────────────────────────────────────────────────────────────────────


# Concepts we'll use to build ratios (names match data/fundamental/concepts.py)
_CONCEPTS_NEEDED = [
    "REVENUE", "NET_INCOME", "GROSS_PROFIT", "OPERATING_INCOME",
    "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY", "CASH",
    "LONG_TERM_DEBT", "CURRENT_ASSETS", "CURRENT_LIABILITIES",
    "EPS_BASIC", "EPS_DILUTED", "SHARES_OUTSTANDING",
    "CFO", "FREE_CASH_FLOW",
]


def _load_fundamental_panel(
    session: Session, market: str, tickers: list[str],
) -> dict[str, pd.DataFrame]:
    """Return {ticker: DataFrame[period_end, period_kind, concept, value]}."""
    if not tickers:
        return {}
    stmt = (
        select(
            FinancialFact.ticker, FinancialFact.period_end,
            FinancialFact.period_kind, FinancialFact.concept,
            FinancialFact.value, FinancialFact.as_of_ts,
        )
        .where(
            FinancialFact.market == market,
            FinancialFact.ticker.in_(tickers),
            FinancialFact.concept.in_(_CONCEPTS_NEEDED),
        )
        .order_by(FinancialFact.ticker, FinancialFact.period_end)
    )
    rows = list(session.execute(stmt).all())
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["ticker", "period_end", "period_kind",
                                      "concept", "value", "as_of_ts"])
    df["value"] = df["value"].astype(float)
    return {t: g for t, g in df.groupby("ticker")}


def _latest_concept_as_of(
    panel: pd.DataFrame, concept: str, as_of: DateType, kind: str = "Q"
) -> Optional[float]:
    sub = panel[(panel["concept"] == concept) & (panel["period_kind"] == kind)]
    if sub.empty:
        return None
    eligible = sub[sub["as_of_ts"].dt.date <= as_of]
    if eligible.empty:
        return None
    return float(eligible.sort_values("as_of_ts").iloc[-1]["value"])


def compute_fundamental_features(
    panel: pd.DataFrame, dates: pd.DatetimeIndex, market_close_price: pd.Series,
) -> pd.DataFrame:
    """For each row date, compute ratios using most recent fundamental panel.

    panel: rows for ONE ticker.
    market_close_price: aligned price series (used for PE, PB).
    """
    feat = pd.DataFrame(index=dates)
    feat["pe_ttm"] = np.nan
    feat["pb"] = np.nan
    feat["ev_ebitda"] = np.nan
    feat["roe_q"] = np.nan
    feat["roa_q"] = np.nan
    feat["debt_equity"] = np.nan
    feat["current_ratio"] = np.nan
    feat["gross_margin"] = np.nan
    feat["rev_yoy"] = np.nan
    feat["eps_yoy"] = np.nan

    for dt in dates:
        d = dt.date() if hasattr(dt, "date") else dt
        rev = _latest_concept_as_of(panel, "REVENUE", d, "Q")
        ni = _latest_concept_as_of(panel, "NET_INCOME", d, "Q")
        gp = _latest_concept_as_of(panel, "GROSS_PROFIT", d, "Q")
        oi = _latest_concept_as_of(panel, "OPERATING_INCOME", d, "Q")
        eps = _latest_concept_as_of(panel, "EPS_DILUTED", d, "Q") \
            or _latest_concept_as_of(panel, "EPS_BASIC", d, "Q")
        assets = _latest_concept_as_of(panel, "TOTAL_ASSETS", d, "Q")
        equity = _latest_concept_as_of(panel, "TOTAL_EQUITY", d, "Q")
        debt = _latest_concept_as_of(panel, "LONG_TERM_DEBT", d, "Q")
        cur_a = _latest_concept_as_of(panel, "CURRENT_ASSETS", d, "Q")
        cur_l = _latest_concept_as_of(panel, "CURRENT_LIABILITIES", d, "Q")
        cash = _latest_concept_as_of(panel, "CASH", d, "Q")
        shares = _latest_concept_as_of(panel, "SHARES_OUTSTANDING", d, "Q")
        price = float(market_close_price.get(dt)) if dt in market_close_price.index else np.nan

        # TTM-ish proxy: 4 × latest Q (rough but cheap)
        if eps and eps != 0 and price and not np.isnan(price):
            feat.at[dt, "pe_ttm"] = float(price) / (eps * 4)
        if equity and equity > 0 and shares and shares > 0 and price and not np.isnan(price):
            book_per_share = equity / shares
            if book_per_share > 0:
                feat.at[dt, "pb"] = float(price) / book_per_share
        if oi and oi > 0 and assets:
            ev = (assets - (cash or 0)) + (debt or 0)
            feat.at[dt, "ev_ebitda"] = ev / (oi * 4)  # OPERATING_INCOME proxy for EBITDA
        if ni and equity and equity > 0:
            feat.at[dt, "roe_q"] = (ni * 4) / equity
        if ni and assets and assets > 0:
            feat.at[dt, "roa_q"] = (ni * 4) / assets
        if debt is not None and equity and equity > 0:
            feat.at[dt, "debt_equity"] = debt / equity
        if cur_a and cur_l and cur_l > 0:
            feat.at[dt, "current_ratio"] = cur_a / cur_l
        if gp and rev and rev > 0:
            feat.at[dt, "gross_margin"] = gp / rev

        # YoY growth — compare latest Q to Q at (d - 365 days)
        rev_prev = _latest_concept_as_of(
            panel, "REVENUE", d - timedelta(days=365), "Q",
        )
        if rev and rev_prev and rev_prev > 0:
            feat.at[dt, "rev_yoy"] = rev / rev_prev - 1.0
        eps_prev = _latest_concept_as_of(
            panel, "EPS_DILUTED", d - timedelta(days=365), "Q",
        ) or _latest_concept_as_of(
            panel, "EPS_BASIC", d - timedelta(days=365), "Q",
        )
        if eps and eps_prev and eps_prev != 0:
            feat.at[dt, "eps_yoy"] = eps / eps_prev - 1.0

    return feat


# ──────────────────────────────────────────────────────────────────────
# Information features
# ──────────────────────────────────────────────────────────────────────


def compute_info_features(
    session: Session, market: str, ticker: str, dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Per-date trailing news counts + sentiment averages."""
    feat = pd.DataFrame(index=dates, columns=[
        "news_count_7d", "news_count_30d",
        "news_sentiment_7d", "news_pos_count_7d", "news_neg_count_7d",
        "news_impact_7d",
    ], dtype=float)

    # One pull of all mention+classification rows for this ticker (small)
    stmt = (
        select(
            NewsArticle.published_ts,
            ArticleClassification.sentiment,
            ArticleClassification.impact,
        )
        .join(NewsTickerMention, and_(
            NewsTickerMention.article_id == NewsArticle.id,
            NewsTickerMention.article_published_ts == NewsArticle.published_ts,
        ))
        .join(ArticleClassification, and_(
            ArticleClassification.article_kind == "news",
            ArticleClassification.article_id == NewsArticle.id,
        ), isouter=True)
        .where(NewsTickerMention.market == market, NewsTickerMention.ticker == ticker)
    )
    rows = list(session.execute(stmt).all())
    if not rows:
        return feat.fillna(0.0)

    df = pd.DataFrame(rows, columns=["ts", "sentiment", "impact"])
    df["ts"] = pd.to_datetime(df["ts"])
    df["sent_num"] = df["sentiment"].map(
        {"POSITIVE": 1.0, "NEUTRAL": 0.0, "NEGATIVE": -1.0}
    ).fillna(0.0)
    df["impact_num"] = df["impact"].map(
        {"HIGH": 1.0, "MEDIUM": 0.5, "LOW": 0.2}
    ).fillna(0.3)

    for dt in dates:
        end = pd.Timestamp(dt).tz_localize("UTC") if dt.tzinfo is None else pd.Timestamp(dt)
        win7 = df[(df["ts"] > end - pd.Timedelta(days=7)) & (df["ts"] <= end)]
        win30 = df[(df["ts"] > end - pd.Timedelta(days=30)) & (df["ts"] <= end)]
        feat.at[dt, "news_count_7d"] = float(len(win7))
        feat.at[dt, "news_count_30d"] = float(len(win30))
        feat.at[dt, "news_sentiment_7d"] = float(win7["sent_num"].mean()) if len(win7) else 0.0
        feat.at[dt, "news_pos_count_7d"] = float((win7["sent_num"] > 0).sum())
        feat.at[dt, "news_neg_count_7d"] = float((win7["sent_num"] < 0).sum())
        feat.at[dt, "news_impact_7d"] = float((win7["sent_num"] * win7["impact_num"]).sum())

    return feat.astype(float)


# ──────────────────────────────────────────────────────────────────────
# Cross-asset + regime features
# ──────────────────────────────────────────────────────────────────────


def load_macro_features(session: Session, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """One row per date with VIX/DXY/SP500 derived features."""
    series_codes = ["VIX", "FX_DXY", "IDX_SP500_FRED", "IDX_KOSPI_ECOS", "RATE_US_10Y"]
    stmt = (
        select(MacroSeries.series_code, MacroSeries.ts, MacroSeries.value)
        .where(MacroSeries.series_code.in_(series_codes))
        .order_by(MacroSeries.ts)
    )
    rows = list(session.execute(stmt).all())
    if not rows:
        return pd.DataFrame(index=dates)
    df = pd.DataFrame(rows, columns=["series_code", "ts", "value"])
    df["value"] = df["value"].astype(float)
    df["ts"] = pd.to_datetime(df["ts"])
    panel = df.pivot_table(index="ts", columns="series_code", values="value", aggfunc="first")
    panel = panel.ffill().reindex(dates, method="ffill")

    feat = pd.DataFrame(index=dates)
    if "VIX" in panel:
        feat["vix"] = panel["VIX"]
        feat["vix_5d_chg"] = panel["VIX"].pct_change(5)
    if "FX_DXY" in panel:
        feat["dxy_5d_chg"] = panel["FX_DXY"].pct_change(5)
    if "IDX_SP500_FRED" in panel:
        feat["sp500_21d_ret"] = panel["IDX_SP500_FRED"].pct_change(21)
    if "RATE_US_10Y" in panel:
        feat["us10y"] = panel["RATE_US_10Y"]
        feat["us10y_5d_chg"] = panel["RATE_US_10Y"].diff(5)
    return feat


def load_regime_features(session: Session, market: str, dates: pd.DatetimeIndex) -> pd.DataFrame:
    stmt = (
        select(MarketRegime.ts, MarketRegime.label, MarketRegime.confidence)
        .where(MarketRegime.market == market)
        .order_by(MarketRegime.ts)
    )
    rows = list(session.execute(stmt).all())
    feat = pd.DataFrame(index=dates, columns=["regime_risk_on", "regime_risk_off", "regime_conf"], dtype=float)
    if not rows:
        return feat.fillna({"regime_risk_on": 0, "regime_risk_off": 0, "regime_conf": 0})
    df = pd.DataFrame(rows, columns=["ts", "label", "conf"])
    df["ts"] = pd.to_datetime(df["ts"])
    df["conf"] = df["conf"].astype(float)
    df = df.set_index("ts").reindex(dates, method="ffill")
    feat["regime_risk_on"] = (df["label"] == "RISK_ON").astype(float)
    feat["regime_risk_off"] = (df["label"] == "RISK_OFF").astype(float)
    feat["regime_conf"] = df["conf"].fillna(0.0)
    return feat


# ──────────────────────────────────────────────────────────────────────
# Top-level builder
# ──────────────────────────────────────────────────────────────────────


@dataclass
class FeatureBuildReport:
    market: str
    tickers_processed: int = 0
    rows_emitted: int = 0
    feature_columns: int = 0


def build_feature_matrix(
    session: Session, *, market: str,
    start: DateType, end: DateType,
    tickers: Optional[list[str]] = None,
) -> tuple[pd.DataFrame, FeatureBuildReport]:
    """End-to-end: load + compute features for [start, end] in ``market``.

    Returns (feature_df, report).
    feature_df indexed by RangeIndex with columns
    ['date', 'market', 'ticker', <features>].
    """
    report = FeatureBuildReport(market=market)

    if tickers is None:
        tickers = [
            r[0] for r in session.execute(
                select(Security.ticker).where(
                    Security.market == market, Security.is_active.is_(True),
                )
            )
        ]

    # Pull price panel once for all tickers
    px_stmt = (
        select(
            DailyPrice.ticker, DailyPrice.trade_date,
            DailyPrice.open, DailyPrice.high, DailyPrice.low,
            DailyPrice.close, DailyPrice.volume,
        )
        .where(
            DailyPrice.market == market,
            DailyPrice.ticker.in_(tickers),
            # 200-day buffer so SMA200 fully warms up at `start`
            DailyPrice.trade_date >= start - timedelta(days=300),
            DailyPrice.trade_date <= end,
        )
        .order_by(DailyPrice.ticker, DailyPrice.trade_date)
    )
    px_rows = list(session.execute(px_stmt).all())
    if not px_rows:
        return pd.DataFrame(), report
    px_df = pd.DataFrame(px_rows, columns=["ticker", "trade_date", "open", "high", "low", "close", "volume"])
    px_df["trade_date"] = pd.to_datetime(px_df["trade_date"])

    # Pre-load fundamental panel
    fund_panels = _load_fundamental_panel(session, market, tickers)

    # Pre-load macro + regime (per-market)
    # Use the union of all trade dates as the index
    all_dates = pd.DatetimeIndex(sorted(px_df["trade_date"].unique()))
    macro_feat = load_macro_features(session, all_dates)
    regime_feat = load_regime_features(session, market, all_dates)

    all_blocks: list[pd.DataFrame] = []
    for ticker, group in px_df.groupby("ticker"):
        bars = group.set_index("trade_date").drop(columns=["ticker"]).sort_index()
        if len(bars) < 60:
            continue
        report.tickers_processed += 1

        price_feat = compute_price_features(bars)
        fund_feat = (
            compute_fundamental_features(fund_panels[ticker], bars.index, bars["close"])
            if ticker in fund_panels else pd.DataFrame(index=bars.index)
        )
        info_feat = compute_info_features(session, market, ticker, bars.index)
        block = pd.concat([
            price_feat,
            fund_feat,
            info_feat,
            macro_feat.reindex(bars.index),
            regime_feat.reindex(bars.index),
        ], axis=1)
        idx_dates = pd.to_datetime(block.index).normalize()
        keep = (idx_dates >= pd.Timestamp(start)) & (idx_dates <= pd.Timestamp(end))
        block = block.loc[keep]
        if block.empty:
            continue
        block.insert(0, "ticker", ticker)
        block.insert(0, "market", market)
        block.insert(0, "date", block.index)
        all_blocks.append(block.reset_index(drop=True))

    if not all_blocks:
        return pd.DataFrame(), report
    out = pd.concat(all_blocks, ignore_index=True)
    report.rows_emitted = len(out)
    report.feature_columns = sum(1 for c in out.columns if c not in ("date", "market", "ticker"))
    return out, report
