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
from core.models.disclosures import Disclosure
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


# ── Extra indicators (no pandas-ta dep; pure numpy/pandas) ──
def _aroon(high: pd.Series, low: pd.Series, period: int = 25) -> tuple[pd.Series, pd.Series]:
    aroon_up = high.rolling(period + 1).apply(
        lambda x: 100 * (period - (period - x.argmax())) / period, raw=True
    )
    aroon_dn = low.rolling(period + 1).apply(
        lambda x: 100 * (period - (period - x.argmin())) / period, raw=True
    )
    return aroon_up, aroon_dn


def _cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 21) -> pd.Series:
    mfv = ((close - low) - (high - close)) / (high - low).replace(0, np.nan) * volume
    return mfv.rolling(period).sum() / volume.rolling(period).sum().replace(0, np.nan)


def _mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
    tp = (high + low + close) / 3
    mf = tp * volume
    pos_mf = mf.where(tp > tp.shift(1), 0).rolling(period).sum()
    neg_mf = mf.where(tp < tp.shift(1), 0).rolling(period).sum()
    ratio = pos_mf / neg_mf.replace(0, np.nan)
    return 100 - 100 / (1 + ratio)


def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    return -100 * (hh - close) / (hh - ll).replace(0, np.nan)


def _roc(close: pd.Series, period: int) -> pd.Series:
    return (close - close.shift(period)) / close.shift(period).replace(0, np.nan) * 100


def _bollinger_squeeze(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.Series:
    """Normalised band width — low values = squeeze (volatility compression)."""
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std()
    return (4 * k * sd) / ma.replace(0, np.nan)  # (upper - lower) / middle


def _ulcer_index(close: pd.Series, period: int = 14) -> pd.Series:
    """Pain index — RMS of % drawdowns over period."""
    rolling_max = close.rolling(period).max()
    dd = ((close - rolling_max) / rolling_max.replace(0, np.nan)) * 100
    return ((dd ** 2).rolling(period).mean()) ** 0.5


def _donchian_position(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    return (close - ll) / (hh - ll).replace(0, np.nan)


def compute_price_features(bars: pd.DataFrame) -> pd.DataFrame:
    """~50 price/volume features per ticker from OHLCV bars."""
    open_ = bars["open"].astype(float)
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    volume = bars["volume"].astype(float)
    daily_ret = close.pct_change(1)

    feat = pd.DataFrame(index=bars.index)
    # Returns (multi-horizon)
    for n in (1, 2, 3, 5, 10, 21, 42, 63, 126, 252):
        feat[f"ret_{n}d"] = close.pct_change(n)
    # Realised volatility
    feat["vol_5d"] = daily_ret.rolling(5).std() * np.sqrt(252)
    feat["vol_21d"] = daily_ret.rolling(21).std() * np.sqrt(252)
    feat["vol_63d"] = daily_ret.rolling(63).std() * np.sqrt(252)
    feat["vol_252d"] = daily_ret.rolling(252).std() * np.sqrt(252)
    # Skewness + Kurtosis (return distribution shape)
    feat["ret_skew_21d"] = daily_ret.rolling(21).skew()
    feat["ret_kurt_21d"] = daily_ret.rolling(21).kurt()
    # Sharpe-like (return / vol)
    feat["sharpe_21d"] = (close.pct_change(21)) / feat["vol_21d"].replace(0, np.nan)
    feat["sharpe_63d"] = (close.pct_change(63)) / feat["vol_63d"].replace(0, np.nan)
    # SMA vs price
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    feat["px_vs_sma20"] = close / sma20 - 1.0
    feat["px_vs_sma50"] = close / sma50 - 1.0
    feat["px_vs_sma200"] = close / sma200 - 1.0
    feat["sma50_above_sma200"] = (sma50 > sma200).astype(float)
    # Drawdown
    feat["dd_from_high_63d"] = close / close.rolling(63).max() - 1.0
    feat["dd_from_high_252d"] = close / close.rolling(252).max() - 1.0
    # Range + volume
    feat["range_pct"] = (high - low) / close
    feat["range_pct_5d_avg"] = feat["range_pct"].rolling(5).mean()
    feat["volume_z21"] = (volume - volume.rolling(21).mean()) / volume.rolling(21).std().replace(0, np.nan)
    feat["volume_z63"] = (volume - volume.rolling(63).mean()) / volume.rolling(63).std().replace(0, np.nan)
    # Open-close gap
    feat["gap_pct"] = (open_ - close.shift(1)) / close.shift(1)
    # Classic indicators
    feat["rsi14"] = _rsi(close, 14)
    feat["rsi5"] = _rsi(close, 5)
    macd_line, macd_sig, macd_hist = _macd(close, 12, 26, 9)
    feat["macd_hist"] = macd_hist
    feat["macd_above"] = (macd_line > macd_sig).astype(float)
    feat["bb_pctb"] = _bbands_percent(close, 20, 2.0)
    feat["bb_squeeze"] = _bollinger_squeeze(close, 20, 2.0)
    feat["adx14"] = _adx(high, low, close, 14)
    feat["stoch_k14"] = _stoch_k(high, low, close, 14)
    feat["williams_r14"] = _williams_r(high, low, close, 14)
    feat["mfi14"] = _mfi(high, low, close, volume, 14)
    feat["cmf21"] = _cmf(high, low, close, volume, 21)
    feat["atr_pct"] = _atr(high, low, close, 14) / close
    feat["obv_slope21"] = _obv_slope(close, volume, 21)
    feat["ulcer14"] = _ulcer_index(close, 14)
    feat["donchian_pos_20"] = _donchian_position(high, low, close, 20)
    feat["donchian_pos_55"] = _donchian_position(high, low, close, 55)
    aroon_up, aroon_dn = _aroon(high, low, 25)
    feat["aroon_up"] = aroon_up
    feat["aroon_dn"] = aroon_dn
    feat["aroon_osc"] = aroon_up - aroon_dn
    feat["roc_10"] = _roc(close, 10)
    feat["roc_21"] = _roc(close, 21)
    # Candle pattern bits (simplified)
    body = (close - open_).abs()
    upper_wick = high - close.where(close >= open_, open_)
    lower_wick = close.where(close <= open_, open_) - low
    rng = (high - low).replace(0, np.nan)
    feat["candle_body_pct"] = body / rng
    feat["candle_upper_wick_pct"] = upper_wick / rng
    feat["candle_lower_wick_pct"] = lower_wick / rng
    feat["is_doji"] = (body / rng < 0.1).astype(float)

    return feat


# ──────────────────────────────────────────────────────────────────────
# Fundamental features
# ──────────────────────────────────────────────────────────────────────


# Concepts we'll use to build ratios (names match data/fundamental/concepts.py)
_CONCEPTS_NEEDED = [
    "REVENUE", "COGS", "NET_INCOME", "GROSS_PROFIT", "OPERATING_INCOME",
    "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY", "CASH",
    "LONG_TERM_DEBT", "SHORT_TERM_DEBT", "CURRENT_ASSETS", "CURRENT_LIABILITIES",
    "EPS_BASIC", "EPS_DILUTED", "SHARES_OUTSTANDING",
    "CFO", "CFI", "CFF", "CAPEX", "FREE_CASH_FLOW", "DIVIDENDS_PAID",
    # Wave 2 — extended concepts
    "EBITDA", "DEPRECIATION_AMORT", "INTEREST_EXPENSE", "TAX_EXPENSE",
    "SGA", "RND_EXPENSE", "INVENTORY", "RECEIVABLES", "PAYABLES",
    "PROPERTY_PLANT_EQ", "RETAINED_EARNINGS", "GOODWILL", "INTANGIBLES",
    "MINORITY_INTEREST", "PREFERRED_STOCK", "STOCK_BUYBACK", "STOCK_ISSUED",
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
        # SHARES_OUTSTANDING isn't reliably extracted from EDGAR/DART, so
        # derive share count from NET_INCOME / EPS (EPS := NI / shares).
        # Works for losses too (both negative -> positive ratio).
        if (not shares or shares <= 0) and ni and eps and eps != 0:
            derived = ni / eps
            shares = derived if derived > 0 else shares
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
# Insider transaction features (from parsed Form 4 bodies)
# ──────────────────────────────────────────────────────────────────────


def compute_insider_features(
    session: Session, market: str, ticker: str, dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Buy/sell direction + value + role from parsed Form 4 bodies.

    Requires sec_form4_parser.py to have populated disclosures.body_text
    with structured JSON. Falls back to zeros if body not parsed.
    """
    feat = pd.DataFrame(index=dates, columns=[
        "insider_net_value_30d", "insider_net_value_7d",
        "insider_buys_30d", "insider_sells_30d",
        "insider_ceo_buys_30d", "insider_director_buys_30d",
        "insider_buy_sell_ratio_30d",
    ], dtype=float)

    panel = _load_insider_panel(session, market)
    df = panel.get(ticker)
    if df is None or df.empty:
        return feat.fillna(0.0)

    for dt in dates:
        end = pd.Timestamp(dt)
        win30 = df[(df["filing_date"] > end - pd.Timedelta(days=30))
                   & (df["filing_date"] <= end)]
        win7 = df[(df["filing_date"] > end - pd.Timedelta(days=7))
                  & (df["filing_date"] <= end)]
        feat.at[dt, "insider_net_value_30d"] = float(win30["net_value"].sum())
        feat.at[dt, "insider_net_value_7d"] = float(win7["net_value"].sum())
        feat.at[dt, "insider_buys_30d"] = float(win30["n_buys"].sum())
        feat.at[dt, "insider_sells_30d"] = float(win30["n_sells"].sum())
        feat.at[dt, "insider_ceo_buys_30d"] = float(
            win30[win30["is_ceo"] == 1]["n_buys"].sum()
        )
        feat.at[dt, "insider_director_buys_30d"] = float(
            win30[win30["is_director"] == 1]["n_buys"].sum()
        )
        buys = float(win30["n_buys"].sum())
        sells = float(win30["n_sells"].sum())
        feat.at[dt, "insider_buy_sell_ratio_30d"] = buys / max(sells, 1.0)
    return feat.astype(float)


# ──────────────────────────────────────────────────────────────────────
# Calendar features (no DB needed)
# ──────────────────────────────────────────────────────────────────────


def compute_calendar_features(dates: pd.DatetimeIndex) -> pd.DataFrame:
    feat = pd.DataFrame(index=dates)
    dt = pd.to_datetime(dates)
    feat["dow"] = dt.dayofweek.astype(float)              # 0=Mon
    feat["dom"] = dt.day.astype(float)
    feat["doq"] = ((dt.month - 1) % 3 * 30 + dt.day).astype(float)
    feat["doy"] = dt.dayofyear.astype(float)
    feat["month"] = dt.month.astype(float)
    feat["quarter"] = dt.quarter.astype(float)
    # Days to nearest quarter-end (earnings season approximation)
    quarter_ends = pd.to_datetime(
        [f"{y}-{m}-01" for y in range(2024, 2027) for m in (4, 7, 10)] +
        [f"{y}-01-01" for y in range(2025, 2028)]
    )
    feat["days_to_q_end"] = [
        min((qe - d).days for qe in quarter_ends if qe >= d) if any(qe >= d for qe in quarter_ends) else 999
        for d in dt
    ]
    feat["is_jan"] = (dt.month == 1).astype(float)
    feat["is_dec"] = (dt.month == 12).astype(float)
    return feat


# ──────────────────────────────────────────────────────────────────────
# Cross-asset features (sector ETFs, USDKRW, gold/oil)
# ──────────────────────────────────────────────────────────────────────


CROSS_ASSET_TICKERS = [
    # US sector ETFs
    ("US", "XLK"), ("US", "XLF"), ("US", "XLV"), ("US", "XLE"),
    ("US", "XLY"), ("US", "XLP"), ("US", "XLI"), ("US", "XLB"),
    ("US", "XLU"), ("US", "XLRE"), ("US", "XLC"),
    # Broad
    ("US", "SPY"), ("US", "QQQ"), ("US", "IWM"),
    # Commodities + FX (yfinance/FDR symbols)
    ("MACRO", "GLD"), ("MACRO", "USO"), ("MACRO", "TLT"),
    # KR overnight/foreign-priced proxies (credential-free KR-direction signal)
    ("MACRO", "EWY"), ("MACRO", "SOXX"), ("MACRO", "SMH"),
    ("MACRO", "FXI"), ("MACRO", "MCHI"),
]


_cross_asset_cache: dict[str, pd.DataFrame] = {}


def _load_cross_asset_panel(session: Session, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Wide panel of close prices for cross-asset tickers."""
    key = "default"
    if key in _cross_asset_cache:
        panel = _cross_asset_cache[key]
        return panel.reindex(dates, method="ffill")

    tickers = [t for (_, t) in CROSS_ASSET_TICKERS]
    stmt = (
        select(DailyPrice.ticker, DailyPrice.trade_date, DailyPrice.close)
        .where(DailyPrice.ticker.in_(tickers))
        .order_by(DailyPrice.trade_date)
    )
    rows = list(session.execute(stmt).all())
    if not rows:
        return pd.DataFrame(index=dates)
    df = pd.DataFrame(rows, columns=["ticker", "trade_date", "close"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["close"] = df["close"].astype(float)
    panel = df.pivot_table(index="trade_date", columns="ticker", values="close", aggfunc="last")
    panel = panel.ffill()
    _cross_asset_cache[key] = panel
    return panel.reindex(dates, method="ffill")


def compute_cross_asset_features(
    session: Session, dates: pd.DatetimeIndex, ticker_close: pd.Series,
) -> pd.DataFrame:
    """Relative momentum vs sector ETFs + commodities."""
    panel = _load_cross_asset_panel(session, dates)
    feat = pd.DataFrame(index=dates)
    if panel.empty:
        return feat
    own_close_f = ticker_close.astype(float).reindex(dates)
    own_21d = own_close_f.pct_change(21)
    for tkr in panel.columns:
        ca_21d = panel[tkr].pct_change(21)
        feat[f"rel_{tkr.lower()}_21d"] = own_21d - ca_21d
    if "SPY" in panel.columns:
        own_dr = own_close_f.pct_change(1)
        spy_dr = panel["SPY"].pct_change(1)
        feat["corr_spy_63d"] = own_dr.rolling(63).corr(spy_dr)
    return feat.astype(float)


_fx_panel_cache: dict[str, pd.DataFrame] = {}


def _load_fx_panel(session: Session, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Wide panel of FX levels (USD/KRW, USD/CNY) from macro_series."""
    codes = ["FX_USDKRW", "FX_USDCNY"]
    key = "default"
    if key not in _fx_panel_cache:
        stmt = (
            select(MacroSeries.series_code, MacroSeries.ts, MacroSeries.value)
            .where(MacroSeries.series_code.in_(codes))
            .order_by(MacroSeries.ts)
        )
        rows = list(session.execute(stmt).all())
        if not rows:
            _fx_panel_cache[key] = pd.DataFrame()
        else:
            df = pd.DataFrame(rows, columns=["series_code", "ts", "value"])
            df["value"] = df["value"].astype(float)
            df["ts"] = pd.to_datetime(df["ts"])
            _fx_panel_cache[key] = df.pivot_table(
                index="ts", columns="series_code", values="value", aggfunc="first"
            ).ffill()
    panel = _fx_panel_cache[key]
    if panel.empty:
        return panel
    return panel.reindex(dates, method="ffill")


def _rolling_beta(own_ret: pd.Series, factor_ret: pd.Series, win: int) -> pd.Series:
    """β = Cov(own, factor) / Var(factor) over a trailing window."""
    cov = own_ret.rolling(win, min_periods=max(20, win // 2)).cov(factor_ret)
    var = factor_ret.rolling(win, min_periods=max(20, win // 2)).var()
    return cov / var.where(var > 0)


def compute_beta_features(
    session: Session, dates: pd.DatetimeIndex, ticker_close: pd.Series,
) -> pd.DataFrame:
    """Per-stock rolling betas to FX (USD/KRW, USD/CNY) and KR overnight
    proxies (SOXX/FXI/EWY). Turns market-wide macro *scalars* into a genuine
    cross-sectional axis — exporters (KRW-weak beneficiaries: semis/autos)
    vs domestics differ in FX/China beta. Targets the KR direction gap."""
    feat = pd.DataFrame(index=dates)
    own_ret = ticker_close.astype(float).reindex(dates).pct_change()
    fx = _load_fx_panel(session, dates)
    if not fx.empty:
        if "FX_USDKRW" in fx.columns:
            fr = fx["FX_USDKRW"].pct_change()
            feat["beta_usdkrw_63d"] = _rolling_beta(own_ret, fr, 63)
            feat["beta_usdkrw_126d"] = _rolling_beta(own_ret, fr, 126)
        if "FX_USDCNY" in fx.columns:
            feat["beta_usdcny_63d"] = _rolling_beta(own_ret, fx["FX_USDCNY"].pct_change(), 63)
    ca = _load_cross_asset_panel(session, dates)
    if not ca.empty:
        for sym, suf in (("SOXX", "soxx"), ("FXI", "fxi"), ("EWY", "ewy")):
            if sym in ca.columns:
                feat[f"beta_{suf}_63d"] = _rolling_beta(own_ret, ca[sym].pct_change(), 63)
    return feat.astype(float)


def compute_liquidity_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Amihud illiquidity (|ret| / dollar-volume) from on-disk OHLCV — a
    documented cross-sectional premium, orthogonal to momentum/vol the tree
    already splits on; especially informative for KR small/mid caps."""
    feat = pd.DataFrame(index=bars.index)
    close = bars["close"].astype(float)
    vol = bars["volume"].astype(float)
    abs_ret = close.pct_change().abs()
    dollar_vol = (close * vol).where(lambda x: x > 0)
    illiq = (abs_ret / dollar_vol) * 1e9  # scaled for numerical range
    feat["amihud_illiq_21d"] = illiq.rolling(21, min_periods=10).mean()
    feat["amihud_illiq_63d"] = illiq.rolling(63, min_periods=21).mean()
    a21 = feat["amihud_illiq_21d"]
    feat["amihud_illiq_z_60d"] = (
        (a21 - a21.rolling(60, min_periods=20).mean())
        / a21.rolling(60, min_periods=20).std().where(lambda x: x > 0)
    )
    return feat.astype(float)


# ──────────────────────────────────────────────────────────────────────
# SEC disclosure features (insider Form 4 + 8-K event counts)
# ──────────────────────────────────────────────────────────────────────


_disclosure_cache: dict[str, dict[str, pd.DataFrame]] = {}
_insider_cache: dict[str, dict[str, pd.DataFrame]] = {}


def _load_disclosure_panel(session: Session, market: str) -> dict[str, pd.DataFrame]:
    """One bulk SELECT per market → cache by ticker. Saves N+1 queries."""
    if market in _disclosure_cache:
        return _disclosure_cache[market]
    stmt = (
        select(Disclosure.ticker, Disclosure.filing_date, Disclosure.filing_type_canonical)
        .where(Disclosure.market == market)
    )
    rows = list(session.execute(stmt).all())
    df = pd.DataFrame(rows, columns=["ticker", "filing_date", "canonical"])
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    cache = {t: g for t, g in df.groupby("ticker")}
    _disclosure_cache[market] = cache
    return cache


def _load_insider_panel_kr(session: Session) -> dict[str, pd.DataFrame]:
    """KR insider panel from `insider_transactions` (DART 임원·주요주주
    소유보고 — the Form-4 equivalent). price_per_share is unreliable
    (mostly 0), so net_value uses signed share count as the value proxy.
    transaction_type: 'P' = 취득(buy), 'S' = 처분(sell). Role strings
    are Korean: 대표* → CEO, contains 이사 → director."""
    from sqlalchemy import text
    rows = list(session.execute(text(
        "SELECT ticker, trade_date, transaction_type, shares, role "
        "FROM insider_transactions WHERE market = 'KR'"
    )).all())
    if not rows:
        return {}
    out: list[dict] = []
    for ticker, td, ttype, shares, role in rows:
        shares = float(shares or 0)
        is_buy = (ttype == "P")
        role_s = str(role or "")
        out.append({
            "ticker": ticker,
            "filing_date": td,
            "net_value": shares if is_buy else -shares,
            "n_buys": 1 if is_buy else 0,
            "n_sells": 0 if is_buy else 1,
            "is_ceo": 1 if "대표" in role_s else 0,
            "is_director": 1 if "이사" in role_s else 0,
        })
    df = pd.DataFrame(out)
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    return {t: g for t, g in df.groupby("ticker")}


def _load_insider_panel(session: Session, market: str) -> dict[str, pd.DataFrame]:
    """Parsed Form 4 bodies (body_fetched=True). Cache by ticker."""
    import json as _json
    if market in _insider_cache:
        return _insider_cache[market]
    if market == "KR":
        cache = _load_insider_panel_kr(session)
        _insider_cache[market] = cache
        return cache
    if market != "US":
        _insider_cache[market] = {}
        return {}
    stmt = (
        select(Disclosure.ticker, Disclosure.filing_date, Disclosure.body_text)
        .where(
            Disclosure.market == "US",
            Disclosure.filing_type_canonical.in_(["INSIDER", "INSIDER_FORM4"]),
            Disclosure.body_fetched.is_(True),
            Disclosure.body_text.isnot(None),
        )
    )
    rows = list(session.execute(stmt).all())
    if not rows:
        _insider_cache[market] = {}
        return {}
    out: list[dict] = []
    for ticker, fd, body in rows:
        try:
            parsed = _json.loads(body)
        except Exception:
            continue
        net_value = 0.0
        n_buys = 0
        n_sells = 0
        is_ceo = 1 if parsed.get("officer_title") and "ceo" in str(parsed.get("officer_title")).lower() else 0
        is_director = 1 if parsed.get("is_director") else 0
        for tx in parsed.get("transactions", []):
            ad = tx.get("a_or_d", "")
            value = float(tx.get("value", 0.0))
            if ad == "A":
                net_value += value
                n_buys += 1
            elif ad == "D":
                net_value -= value
                n_sells += 1
        out.append({
            "ticker": ticker, "filing_date": fd,
            "net_value": net_value, "n_buys": n_buys, "n_sells": n_sells,
            "is_ceo": is_ceo, "is_director": is_director,
        })
    df = pd.DataFrame(out)
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    cache = {t: g for t, g in df.groupby("ticker")}
    _insider_cache[market] = cache
    return cache


def compute_disclosure_features(
    session: Session, market: str, ticker: str, dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Trailing counts of insider (Form 4) + 8-K filings."""
    feat = pd.DataFrame(index=dates, columns=[
        "insider_count_7d", "insider_count_30d",
        "event_8k_count_7d", "event_8k_count_30d",
        "days_since_last_10k", "days_since_last_10q",
    ], dtype=float)

    if market != "US":
        return feat.fillna(0.0)

    panel = _load_disclosure_panel(session, market)
    df = panel.get(ticker)
    if df is None or df.empty:
        return feat.fillna({"insider_count_7d": 0, "insider_count_30d": 0,
                             "event_8k_count_7d": 0, "event_8k_count_30d": 0,
                             "days_since_last_10k": 9999, "days_since_last_10q": 9999})

    insider = df[df["canonical"].isin(["INSIDER", "INSIDER_FORM4"])]
    event_8k = df[df["canonical"].isin(["MATERIAL_EVENT", "EVENT_8K"])]
    annual = df[df["canonical"].isin(["ANNUAL"])]
    quarterly = df[df["canonical"].isin(["QUARTERLY"])]

    for dt in dates:
        end = pd.Timestamp(dt)
        feat.at[dt, "insider_count_7d"] = float(
            ((insider["filing_date"] > end - pd.Timedelta(days=7))
             & (insider["filing_date"] <= end)).sum()
        )
        feat.at[dt, "insider_count_30d"] = float(
            ((insider["filing_date"] > end - pd.Timedelta(days=30))
             & (insider["filing_date"] <= end)).sum()
        )
        feat.at[dt, "event_8k_count_7d"] = float(
            ((event_8k["filing_date"] > end - pd.Timedelta(days=7))
             & (event_8k["filing_date"] <= end)).sum()
        )
        feat.at[dt, "event_8k_count_30d"] = float(
            ((event_8k["filing_date"] > end - pd.Timedelta(days=30))
             & (event_8k["filing_date"] <= end)).sum()
        )
        prior_annual = annual[annual["filing_date"] <= end]
        prior_q = quarterly[quarterly["filing_date"] <= end]
        feat.at[dt, "days_since_last_10k"] = float(
            (end - prior_annual["filing_date"].max()).days
            if len(prior_annual) else 9999
        )
        feat.at[dt, "days_since_last_10q"] = float(
            (end - prior_q["filing_date"].max()).days
            if len(prior_q) else 9999
        )

    return feat.astype(float)


# ──────────────────────────────────────────────────────────────────────
# Cross-asset + regime features
# ──────────────────────────────────────────────────────────────────────


def load_macro_features(session: Session, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Macro panel — VIX/DXY/SP500/Yield curve/CPI etc.

    Expanded from 6 to ~14 features now that FRED + BOK ECOS feeds are
    populated. Adds yield curve (2Y, slope 10Y-2Y), fed funds, CPI YoY,
    M2 growth, unemployment, and KR base rate.
    """
    series_codes = [
        "VIX", "FX_DXY", "IDX_SP500_FRED", "IDX_KOSPI_ECOS",
        "RATE_US_10Y", "RATE_US_2Y", "RATE_US_3M", "FEDFUNDS_US",
        "CPI_US", "M2_US", "UNRATE_US",
        "RATE_KR_BASE", "CPI_KR",
        # Wave 4 — macro/cross-asset depth (raw already in macro_series)
        "RATE_US_10Y_TIPS", "BREAKEVEN_INFLATION_10Y", "HY_CREDIT_SPREAD",
        "COPPER", "WTI_OIL", "NAT_GAS",
        "RATE_US_5Y", "RATE_US_30Y",
        "FX_USDKRW", "FX_USDJPY",
        # KR-native regime block: govt bond curve + KOSPI (realized-vol proxy)
        "RATE_KR_3Y", "RATE_KR_5Y", "RATE_KR_10Y", "IDX_KOSPI_ECOS",
    ]
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
        feat["vix_21d_chg"] = panel["VIX"].pct_change(21)
    if "FX_DXY" in panel:
        feat["dxy_5d_chg"] = panel["FX_DXY"].pct_change(5)
        feat["dxy_21d_chg"] = panel["FX_DXY"].pct_change(21)
    if "IDX_SP500_FRED" in panel:
        feat["sp500_21d_ret"] = panel["IDX_SP500_FRED"].pct_change(21)
        feat["sp500_63d_ret"] = panel["IDX_SP500_FRED"].pct_change(63)
    if "RATE_US_10Y" in panel:
        feat["us10y"] = panel["RATE_US_10Y"]
        feat["us10y_5d_chg"] = panel["RATE_US_10Y"].diff(5)
    if "RATE_US_2Y" in panel:
        feat["us2y"] = panel["RATE_US_2Y"]
    if "RATE_US_10Y" in panel and "RATE_US_2Y" in panel:
        feat["yield_curve_2_10"] = panel["RATE_US_10Y"] - panel["RATE_US_2Y"]
    if "FEDFUNDS_US" in panel:
        feat["fedfunds"] = panel["FEDFUNDS_US"]
    if "CPI_US" in panel:
        feat["cpi_us_yoy"] = panel["CPI_US"].pct_change(12)
    if "M2_US" in panel:
        feat["m2_us_yoy"] = panel["M2_US"].pct_change(12)
    if "UNRATE_US" in panel:
        feat["unrate_us"] = panel["UNRATE_US"]
        feat["unrate_us_chg"] = panel["UNRATE_US"].diff(3)
    if "RATE_KR_BASE" in panel:
        feat["kr_base_rate"] = panel["RATE_KR_BASE"]
    if "CPI_KR" in panel:
        feat["cpi_kr_yoy"] = panel["CPI_KR"].pct_change(12)

    # ── Wave 4 — macro/cross-asset depth ──────────────────────────────
    # Real yield (TIPS) — level + momentum. Falling real yields = risk-on.
    if "RATE_US_10Y_TIPS" in panel:
        feat["real_yield_10y"] = panel["RATE_US_10Y_TIPS"]
        feat["real_yield_21d_chg"] = panel["RATE_US_10Y_TIPS"].diff(21)
    # Inflation breakeven — level + momentum.
    if "BREAKEVEN_INFLATION_10Y" in panel:
        feat["breakeven_10y"] = panel["BREAKEVEN_INFLATION_10Y"]
        feat["breakeven_21d_chg"] = panel["BREAKEVEN_INFLATION_10Y"].diff(21)
    # High-yield credit spread — widening = risk-off (strong signal).
    if "HY_CREDIT_SPREAD" in panel:
        feat["hy_credit_spread"] = panel["HY_CREDIT_SPREAD"]
        feat["hy_credit_5d_chg"] = panel["HY_CREDIT_SPREAD"].diff(5)
        feat["hy_credit_21d_chg"] = panel["HY_CREDIT_SPREAD"].diff(21)
    # Dr. Copper — leading growth proxy (monthly series; ffill'd).
    if "COPPER" in panel:
        feat["copper_63d_ret"] = panel["COPPER"].pct_change(63)
    # Energy — direct WTI / nat-gas momentum (not just the USO/sector ETF).
    if "WTI_OIL" in panel:
        feat["wti_21d_ret"] = panel["WTI_OIL"].pct_change(21)
    if "NAT_GAS" in panel:
        feat["natgas_21d_ret"] = panel["NAT_GAS"].pct_change(21)
    # Yield-curve shape from the 10y-deep tenors (5Y/10Y/30Y; 2Y/3M are
    # only 2024+). Litterman-Scheinkman level/slope/curvature analog.
    if "RATE_US_5Y" in panel and "RATE_US_30Y" in panel:
        feat["yield_curve_5_30"] = panel["RATE_US_30Y"] - panel["RATE_US_5Y"]
    if all(c in panel for c in ("RATE_US_5Y", "RATE_US_10Y", "RATE_US_30Y")):
        feat["yield_curvature"] = (2 * panel["RATE_US_10Y"]
                                   - panel["RATE_US_5Y"] - panel["RATE_US_30Y"])
    # FX momentum beyond DXY — won/yen carry-relevant.
    if "FX_USDKRW" in panel:
        feat["usdkrw_21d_chg"] = panel["FX_USDKRW"].pct_change(21)
    if "FX_USDJPY" in panel:
        feat["usdjpy_21d_chg"] = panel["FX_USDJPY"].pct_change(21)
    # VIX percentile in trailing 252d — regime-relative fear (rank beats level).
    if "VIX" in panel:
        feat["vix_pctile_252d"] = panel["VIX"].rolling(252, min_periods=63).rank(pct=True)
    # Volatility Risk Premium — implied (VIX) minus realised SP500 vol.
    if "VIX" in panel and "IDX_SP500_FRED" in panel:
        rv = (panel["IDX_SP500_FRED"].pct_change()
              .rolling(21, min_periods=10).std() * (252 ** 0.5) * 100.0)
        feat["vol_risk_premium"] = panel["VIX"] - rv
    # Funding stress — 3M T-bill minus fed funds (short history, guarded).
    if "RATE_US_3M" in panel and "FEDFUNDS_US" in panel:
        feat["funding_stress"] = panel["RATE_US_3M"] - panel["FEDFUNDS_US"]
    # ── KR-native regime block ────────────────────────────────────────
    # KR govt-bond curve: level + slope (3Y-10Y). A domestic term-spread
    # voter the regime model wholly lacked (it only had the US curve).
    if "RATE_KR_10Y" in panel:
        feat["kr_10y"] = panel["RATE_KR_10Y"]
        feat["kr_10y_21d_chg"] = panel["RATE_KR_10Y"].diff(21)
    if "RATE_KR_3Y" in panel and "RATE_KR_10Y" in panel:
        feat["kr_term_spread_3_10"] = panel["RATE_KR_10Y"] - panel["RATE_KR_3Y"]
    # KOSPI realized vol — free proxy for the (KRX-login-walled) VKOSPI fear
    # gauge. Honest substitute: realized, not implied, but KR-native stress.
    if "IDX_KOSPI_ECOS" in panel:
        krv = (panel["IDX_KOSPI_ECOS"].pct_change()
               .rolling(21, min_periods=10).std() * (252 ** 0.5) * 100.0)
        feat["kospi_realized_vol_21d"] = krv
        feat["kospi_rv_pctile_252d"] = krv.rolling(252, min_periods=63).rank(pct=True)
    return feat


def load_regime_features(session: Session, market: str, dates: pd.DatetimeIndex) -> pd.DataFrame:
    stmt = (
        select(MarketRegime.ts, MarketRegime.label, MarketRegime.confidence)
        .where(MarketRegime.market == market)
        .order_by(MarketRegime.ts)
    )
    rows = list(session.execute(stmt).all())
    cols = ["regime_risk_on", "regime_risk_off", "regime_conf",
            "regime_calm_bull", "regime_neutral", "regime_crisis"]
    feat = pd.DataFrame(index=dates, columns=cols, dtype=float)
    if not rows:
        return feat.fillna(0.0)
    df = pd.DataFrame(rows, columns=["ts", "label", "conf"])
    df["ts"] = pd.to_datetime(df["ts"])
    df["conf"] = df["conf"].astype(float)
    df = df.set_index("ts").reindex(dates, method="ffill")
    # HMM 5-state labels are lowercase: calm_bull/risk_on/neutral/risk_off/crisis.
    # (Also tolerate legacy uppercase RISK_ON/RISK_OFF.)
    lab = df["label"].astype(str).str.lower()
    feat["regime_risk_on"] = lab.isin(["risk_on", "calm_bull"]).astype(float)
    feat["regime_risk_off"] = lab.isin(["risk_off", "crisis"]).astype(float)
    feat["regime_conf"] = df["conf"].fillna(0.0)
    feat["regime_calm_bull"] = (lab == "calm_bull").astype(float)
    feat["regime_neutral"] = (lab == "neutral").astype(float)
    feat["regime_crisis"] = (lab == "crisis").astype(float)
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
        for c in ("open", "high", "low", "close", "volume"):
            if c in bars.columns:
                bars[c] = bars[c].astype(float)
        if len(bars) < 60:
            continue
        report.tickers_processed += 1

        price_feat = compute_price_features(bars)
        fund_feat = (
            compute_fundamental_features(fund_panels[ticker], bars.index, bars["close"])
            if ticker in fund_panels else pd.DataFrame(index=bars.index)
        )
        info_feat = compute_info_features(session, market, ticker, bars.index)
        disc_feat = compute_disclosure_features(session, market, ticker, bars.index)
        insider_feat = compute_insider_features(session, market, ticker, bars.index)
        cal_feat = compute_calendar_features(bars.index)
        cross_feat = compute_cross_asset_features(session, bars.index, bars["close"])
        # New: per-stock FX/overnight-proxy betas + Amihud liquidity (on-disk).
        beta_feat = compute_beta_features(session, bars.index, bars["close"])
        liq_feat = compute_liquidity_features(bars)
        # Wave 1 — advanced features (Technical extras / Stats / Micro / FS composites)
        from training.features_advanced import (
            compute_extra_technical, compute_stat_features,
            compute_microstructure, compute_advanced_fundamental,
        )
        extra_tech = compute_extra_technical(bars)
        # Use market panel from cross-asset cache (SPY for US, fall back)
        try:
            from training.features import _load_cross_asset_panel
            ca_panel = _load_cross_asset_panel(session, bars.index)
            mkt_close = ca_panel["SPY"] if "SPY" in ca_panel.columns else bars["close"].astype(float)
        except Exception:
            mkt_close = bars["close"].astype(float)
        stat_feat = compute_stat_features(bars, mkt_close)
        micro_feat = compute_microstructure(bars)
        adv_fund = (
            compute_advanced_fundamental(fund_panels[ticker], bars.index, bars["close"])
            if ticker in fund_panels else pd.DataFrame(index=bars.index)
        )
        # Event calendar flags (FOMC/CPI/NFP/PCE/GDP/BOK)
        from training.features_calendar import compute_event_calendar_features
        evt_cal = compute_event_calendar_features(bars.index, market)
        evt_cal = evt_cal.reindex(bars.index)
        # GDELT V2Tone aggregator (US only — KR rarely tagged in GDELT)
        try:
            from training.features_gdelt import compute_gdelt_features
            gdelt_feat = compute_gdelt_features(session, market, ticker, bars.index)
        except Exception:
            gdelt_feat = pd.DataFrame(index=bars.index)
        # Wave 2 — comprehensive fundamental (~50 features)
        try:
            from training.features_fundamental_v2 import compute_fundamental_v2
            fund_v2 = (
                compute_fundamental_v2(fund_panels[ticker], bars.index, bars["close"])
                if ticker in fund_panels else pd.DataFrame(index=bars.index)
            )
        except Exception:
            fund_v2 = pd.DataFrame(index=bars.index)
        # Wave 2 — technical v2 (Ichimoku/Divergence/TTM/Pivot/OrderFlow/Accel/SR)
        try:
            from training.features_technical_v2 import compute_technical_v2
            tech_v2 = compute_technical_v2(bars)
        except Exception as _ex:
            import logging
            logging.getLogger(__name__).warning("tech_v2 failed: %s", _ex)
            tech_v2 = pd.DataFrame(index=bars.index)
        # Wave 2 — information v2 (news/insider/SEC text)
        try:
            from training.features_information_v2 import compute_information_v2
            info_v2 = compute_information_v2(session, market, ticker, bars.index)
        except Exception:
            info_v2 = pd.DataFrame(index=bars.index)
        # Wave 2 — alt data (Short/Options/Wiki/Trends/Reddit/Patents/13F/GCAM)
        try:
            from training.features_alt_data import compute_alt_data_features
            alt_feat = compute_alt_data_features(session, market, ticker, bars.index)
        except Exception:
            alt_feat = pd.DataFrame(index=bars.index)
        # Wave 3 — Wavelet/STL/PCA decomposition embeddings
        try:
            from training.features_embeddings import compute_embedding_features
            emb_feat = compute_embedding_features(bars, include_ae=False)
        except Exception:
            emb_feat = pd.DataFrame(index=bars.index)
        # Wave 3 — FinBERT 12 sentiment aggregator features
        try:
            from training.features_finbert_agg import compute_finbert_features
            finbert_feat = compute_finbert_features(session, market, ticker, bars.index)
        except Exception:
            finbert_feat = pd.DataFrame(index=bars.index)
        block = pd.concat([
            price_feat,
            fund_feat,
            adv_fund,
            info_feat,
            disc_feat,
            insider_feat,
            cal_feat,
            cross_feat,
            beta_feat,
            liq_feat,
            extra_tech,
            stat_feat,
            micro_feat,
            evt_cal,
            gdelt_feat,
            fund_v2,
            tech_v2,
            info_v2,
            alt_feat,
            emb_feat,
            finbert_feat,
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
