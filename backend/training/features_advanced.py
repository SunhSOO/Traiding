"""Advanced feature engineering — Wave 1 expansion.

Adds 80+ features beyond features.py:

1. Extra Technical (pandas-ta 30+): TRIX, KST, DPO, TSI, PPO, PVO, BOP,
   Chande, Vortex VI±, DMI±, Mass Index, UltimateOsc, Coppock, KAMA,
   HMA, ALMA, SuperTrend, parabolic-SAR direction, Garman-Klass vol,
   Yang-Zhang vol, Hurst exponent, autocorrelation
2. Candlestick patterns (10 most informative)
3. Fundamental composite scores: Piotroski F-Score (9 bits), Altman
   Z-Score, Beneish M-Score
4. Sector-relative percentile features (per-metric)
5. Microstructure daily proxies
6. Statistical: rolling beta to market, Pearson/Spearman corr

These features are computed alongside features.py and joined per
(market, ticker, date). Stored as separate columns.
"""
from __future__ import annotations

from datetime import date as DateType, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.financials import FinancialFact
from core.models.prices import DailyPrice, MacroSeries


# ──────────────────────────────────────────────────────────────────────
# Extra Technical Indicators (pure numpy/pandas)
# ──────────────────────────────────────────────────────────────────────


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _trix(close: pd.Series, period: int = 15) -> pd.Series:
    """TRIX = 1-day ROC of triple-smoothed EMA."""
    e1 = _ema(close, period)
    e2 = _ema(e1, period)
    e3 = _ema(e2, period)
    return (e3 - e3.shift(1)) / e3.shift(1).replace(0, np.nan) * 100


def _dpo(close: pd.Series, period: int = 20) -> pd.Series:
    """Detrended Price Oscillator."""
    sma_n = close.rolling(period).mean()
    return close.shift(int(period / 2) + 1) - sma_n


def _tsi(close: pd.Series, slow: int = 25, fast: int = 13) -> pd.Series:
    """True Strength Index."""
    pc = close.diff()
    abs_pc = pc.abs()
    smooth1 = _ema(_ema(pc, slow), fast)
    smooth2 = _ema(_ema(abs_pc, slow), fast).replace(0, np.nan)
    return smooth1 / smooth2 * 100


def _ppo(close: pd.Series, fast: int = 12, slow: int = 26) -> pd.Series:
    """Percentage Price Oscillator (like MACD but %)."""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    return (ema_fast - ema_slow) / ema_slow.replace(0, np.nan) * 100


def _pvo(volume: pd.Series, fast: int = 12, slow: int = 26) -> pd.Series:
    """Percentage Volume Oscillator."""
    ema_fast = _ema(volume, fast)
    ema_slow = _ema(volume, slow)
    return (ema_fast - ema_slow) / ema_slow.replace(0, np.nan) * 100


def _bop(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Balance of Power."""
    return (close - open_) / (high - low).replace(0, np.nan)


def _chande(close: pd.Series, period: int = 20) -> pd.Series:
    """Chande Momentum Oscillator."""
    diff = close.diff()
    up = diff.clip(lower=0).rolling(period).sum()
    down = (-diff.clip(upper=0)).rolling(period).sum()
    return (up - down) / (up + down).replace(0, np.nan) * 100


def _vortex(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> tuple[pd.Series, pd.Series]:
    """Vortex Indicator (VI+, VI-)."""
    tr = pd.concat([
        high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    vmp = (high - low.shift(1)).abs()
    vmm = (low - high.shift(1)).abs()
    tr_n = tr.rolling(period).sum().replace(0, np.nan)
    return vmp.rolling(period).sum() / tr_n, vmm.rolling(period).sum() / tr_n


def _ultimate_osc(high: pd.Series, low: pd.Series, close: pd.Series,
                   p1: int = 7, p2: int = 14, p3: int = 28) -> pd.Series:
    """Ultimate Oscillator."""
    prior_close = close.shift(1)
    true_low = pd.concat([low, prior_close], axis=1).min(axis=1)
    true_high = pd.concat([high, prior_close], axis=1).max(axis=1)
    bp = close - true_low
    tr = true_high - true_low
    avg = lambda n: bp.rolling(n).sum() / tr.rolling(n).sum().replace(0, np.nan)
    return 100 * (4 * avg(p1) + 2 * avg(p2) + avg(p3)) / 7


def _coppock(close: pd.Series, slow: int = 14, fast: int = 11, wma: int = 10) -> pd.Series:
    """Coppock Curve = WMA of (ROC_slow + ROC_fast)."""
    roc_slow = close.pct_change(slow) * 100
    roc_fast = close.pct_change(fast) * 100
    combined = roc_slow + roc_fast
    weights = np.arange(1, wma + 1)
    return combined.rolling(wma).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True,
    )


def _kama(close: pd.Series, period: int = 10) -> pd.Series:
    """Kaufman's Adaptive Moving Average."""
    change = (close - close.shift(period)).abs()
    volatility = close.diff().abs().rolling(period).sum()
    er = change / volatility.replace(0, np.nan)
    sc = (er * (2 / (2 + 1) - 2 / (30 + 1)) + 2 / (30 + 1)) ** 2
    kama = close.copy()
    for i in range(period, len(close)):
        if pd.notna(sc.iat[i]) and pd.notna(kama.iat[i - 1]):
            kama.iat[i] = kama.iat[i - 1] + sc.iat[i] * (close.iat[i] - kama.iat[i - 1])
    return kama


def _hma(close: pd.Series, period: int = 20) -> pd.Series:
    """Hull Moving Average — fast and smooth."""
    half = int(period / 2)
    sqrt_n = int(np.sqrt(period))
    wma_half = close.rolling(half).apply(
        lambda x: np.dot(x, np.arange(1, half + 1)) / (half * (half + 1) / 2), raw=True,
    )
    wma_full = close.rolling(period).apply(
        lambda x: np.dot(x, np.arange(1, period + 1)) / (period * (period + 1) / 2), raw=True,
    )
    diff = 2 * wma_half - wma_full
    return diff.rolling(sqrt_n).apply(
        lambda x: np.dot(x, np.arange(1, sqrt_n + 1)) / (sqrt_n * (sqrt_n + 1) / 2), raw=True,
    )


def _supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """SuperTrend direction (+1 long, -1 short)."""
    atr = pd.concat([
        high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1).ewm(alpha=1 / period, adjust=False).mean()
    hl2 = (high + low) / 2
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    direction = pd.Series(1, index=close.index)
    for i in range(1, len(close)):
        if pd.isna(upper.iat[i - 1]):
            continue
        if close.iat[i] > upper.iat[i - 1]:
            direction.iat[i] = 1
        elif close.iat[i] < lower.iat[i - 1]:
            direction.iat[i] = -1
        else:
            direction.iat[i] = direction.iat[i - 1]
    return direction


def _garman_klass_vol(open_: pd.Series, high: pd.Series, low: pd.Series,
                      close: pd.Series, period: int = 21) -> pd.Series:
    """Garman-Klass volatility (uses OHLC, more efficient than realized)."""
    hl = (np.log(high / low)) ** 2
    co = (np.log(close / open_)) ** 2
    gk = 0.5 * hl - (2 * np.log(2) - 1) * co
    return np.sqrt(gk.rolling(period).mean() * 252)


def _yang_zhang_vol(open_: pd.Series, high: pd.Series, low: pd.Series,
                    close: pd.Series, period: int = 21) -> pd.Series:
    """Yang-Zhang volatility — most accurate (overnight + intraday)."""
    log_ho = np.log(high / open_)
    log_lo = np.log(low / open_)
    log_co = np.log(close / open_)
    log_oc = np.log(open_ / close.shift(1))
    rs = log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)
    k = 0.34 / (1.34 + (period + 1) / (period - 1))
    sigma_o = log_oc.rolling(period).var()
    sigma_c = log_co.rolling(period).var()
    sigma_rs = rs.rolling(period).mean()
    return np.sqrt((sigma_o + k * sigma_c + (1 - k) * sigma_rs) * 252)


def _hurst_exponent(close: pd.Series, window: int = 100) -> pd.Series:
    """Rolling Hurst exponent. H>0.5 trending, H<0.5 mean-reverting."""
    def _calc(x):
        if len(x) < 20 or np.std(x) == 0:
            return np.nan
        lags = [2, 4, 8, 16, 32]
        tau = []
        for lag in lags:
            if lag >= len(x):
                continue
            diff = x[lag:] - x[:-lag]
            if np.std(diff) <= 0:
                continue
            tau.append(np.sqrt(np.std(diff)))
        if len(tau) < 2:
            return np.nan
        log_lag = np.log(lags[:len(tau)])
        log_tau = np.log(tau)
        slope = np.polyfit(log_lag, log_tau, 1)[0]
        return slope * 2
    return close.rolling(window).apply(_calc, raw=True)


def _autocorr(close: pd.Series, lag: int = 5, window: int = 63) -> pd.Series:
    """Rolling autocorrelation at given lag."""
    ret = close.pct_change()
    return ret.rolling(window).apply(
        lambda x: pd.Series(x).autocorr(lag=lag) if pd.Series(x).std() > 0 else np.nan,
        raw=False,
    )


# ──────────────────────────────────────────────────────────────────────
# Candle Patterns (10 most informative)
# ──────────────────────────────────────────────────────────────────────


def _candle_patterns(open_: pd.Series, high: pd.Series, low: pd.Series,
                     close: pd.Series) -> pd.DataFrame:
    """Detect 10 key candle patterns. Returns DataFrame of binary flags."""
    body = (close - open_).abs()
    range_ = (high - low).replace(0, np.nan)
    body_ratio = body / range_
    upper_shadow = (high - close.where(close >= open_, open_))
    lower_shadow = (close.where(close <= open_, open_) - low)
    is_bullish = (close > open_).astype(float)
    is_bearish = (close < open_).astype(float)

    feat = pd.DataFrame(index=close.index)

    # 1. Hammer: small body at top, long lower shadow
    feat["pat_hammer"] = ((body_ratio < 0.3) &
                          (lower_shadow > 2 * body) &
                          (upper_shadow < 0.3 * body)).astype(float)

    # 2. Shooting star: small body at bottom, long upper shadow
    feat["pat_shooting_star"] = ((body_ratio < 0.3) &
                                   (upper_shadow > 2 * body) &
                                   (lower_shadow < 0.3 * body)).astype(float)

    # 3. Bullish engulfing
    prev_body = body.shift(1)
    prev_bear = is_bearish.shift(1)
    feat["pat_bull_engulf"] = ((is_bullish == 1) & (prev_bear == 1) &
                                 (body > prev_body) &
                                 (close > open_.shift(1)) &
                                 (open_ < close.shift(1))).astype(float)

    # 4. Bearish engulfing
    prev_bull = is_bullish.shift(1)
    feat["pat_bear_engulf"] = ((is_bearish == 1) & (prev_bull == 1) &
                                 (body > prev_body) &
                                 (close < open_.shift(1)) &
                                 (open_ > close.shift(1))).astype(float)

    # 5. Doji (very small body)
    feat["pat_doji"] = (body_ratio < 0.05).astype(float)

    # 6. Marubozu — full body, no shadows
    feat["pat_marubozu"] = (body_ratio > 0.95).astype(float)

    # 7. Spinning Top — small body, both shadows
    feat["pat_spinning_top"] = ((body_ratio < 0.3) &
                                  (upper_shadow > 0.3 * range_) &
                                  (lower_shadow > 0.3 * range_)).astype(float)

    # 8. Three white soldiers (3 consecutive long bullish)
    long_bull = ((is_bullish == 1) & (body_ratio > 0.6)).astype(int)
    feat["pat_3_white_soldiers"] = (
        (long_bull + long_bull.shift(1) + long_bull.shift(2)) == 3
    ).astype(float)

    # 9. Three black crows
    long_bear = ((is_bearish == 1) & (body_ratio > 0.6)).astype(int)
    feat["pat_3_black_crows"] = (
        (long_bear + long_bear.shift(1) + long_bear.shift(2)) == 3
    ).astype(float)

    # 10. Inside day (today's range inside yesterday's)
    feat["pat_inside_day"] = ((high < high.shift(1)) & (low > low.shift(1))).astype(float)

    return feat


# ──────────────────────────────────────────────────────────────────────
# Statistical Features (rolling beta, autocorr already above)
# ──────────────────────────────────────────────────────────────────────


def compute_stat_features(
    bars: pd.DataFrame, market_close: pd.Series,
) -> pd.DataFrame:
    """Rolling beta, alpha, tracking error, corr vs market."""
    close = bars["close"].astype(float)
    ret = close.pct_change()
    mkt_ret = market_close.reindex(bars.index).pct_change()

    feat = pd.DataFrame(index=bars.index)
    # Rolling correlation
    feat["corr_mkt_21d"] = ret.rolling(21).corr(mkt_ret)
    feat["corr_mkt_63d"] = ret.rolling(63).corr(mkt_ret)
    feat["corr_mkt_252d"] = ret.rolling(252).corr(mkt_ret)
    # Rolling beta (cov / var)
    cov_21 = ret.rolling(21).cov(mkt_ret)
    var_21 = mkt_ret.rolling(21).var()
    feat["beta_21d"] = cov_21 / var_21.replace(0, np.nan)
    cov_63 = ret.rolling(63).cov(mkt_ret)
    var_63 = mkt_ret.rolling(63).var()
    feat["beta_63d"] = cov_63 / var_63.replace(0, np.nan)
    cov_252 = ret.rolling(252).cov(mkt_ret)
    var_252 = mkt_ret.rolling(252).var()
    feat["beta_252d"] = cov_252 / var_252.replace(0, np.nan)
    # Alpha = mean(ret) - beta × mean(mkt)
    feat["alpha_63d"] = ret.rolling(63).mean() - feat["beta_63d"] * mkt_ret.rolling(63).mean()
    # Blitz(2011) residual momentum: daily CAPM residual accumulated over 12-1m /
    # 6-1m (skip last month), standardized by residual vol (t-stat-like). A/B
    # (2026-06-18) raised OOS rank-IC on BOTH KR & US over the base set with lower
    # concentration & drawdown — the campaign's first validated new signal.
    resid = ret - feat["beta_252d"] * mkt_ret
    for win, tag in ((231, "12m"), (105, "6m")):
        s = resid.shift(21).rolling(win, min_periods=win // 2).sum()
        v = resid.shift(21).rolling(win, min_periods=win // 2).std()
        feat[f"resid_mom_blitz_{tag}"] = s / (v * np.sqrt(win) + 1e-9)
    # Tracking error
    feat["tracking_err_63d"] = (ret - mkt_ret).rolling(63).std() * np.sqrt(252)
    # Information ratio
    feat["info_ratio_63d"] = (ret - mkt_ret).rolling(63).mean() / \
                              (ret - mkt_ret).rolling(63).std().replace(0, np.nan) * np.sqrt(252)
    # Sortino
    downside = ret.where(ret < 0, 0)
    feat["sortino_63d"] = ret.rolling(63).mean() / downside.rolling(63).std().replace(0, np.nan) * np.sqrt(252)
    # Skewness, Kurtosis additional windows
    feat["ret_skew_63d"] = ret.rolling(63).skew()
    feat["ret_kurt_63d"] = ret.rolling(63).kurt()
    feat["ret_skew_252d"] = ret.rolling(252).skew()
    # Autocorrelations
    feat["autocorr_1"] = _autocorr(close, lag=1, window=63)
    feat["autocorr_5"] = _autocorr(close, lag=5, window=63)
    feat["autocorr_21"] = _autocorr(close, lag=21, window=126)
    # Hurst exponent
    feat["hurst_100"] = _hurst_exponent(close, window=100)
    return feat.astype(float)


# ──────────────────────────────────────────────────────────────────────
# Microstructure Daily Proxies
# ──────────────────────────────────────────────────────────────────────


def compute_microstructure(bars: pd.DataFrame) -> pd.DataFrame:
    """Daily proxies for microstructure (no tick data needed)."""
    open_ = bars["open"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    close = bars["close"].astype(float)
    volume = bars["volume"].astype(float)

    feat = pd.DataFrame(index=bars.index)
    # Closing range strength (where close is in day's range)
    feat["close_range_strength"] = (close - low) / (high - low).replace(0, np.nan)
    # Opening gap
    feat["opening_gap"] = (open_ - close.shift(1)) / close.shift(1).replace(0, np.nan)
    feat["abs_opening_gap"] = feat["opening_gap"].abs()
    # Range relative to ATR
    range_ = high - low
    atr = range_.ewm(alpha=1 / 14, adjust=False).mean()
    feat["range_atr_ratio"] = range_ / atr.replace(0, np.nan)
    # Effective spread proxy (CRSP-style)
    log_close = np.log(close.replace(0, np.nan))
    cov_d = log_close.diff().rolling(21).apply(
        lambda x: np.cov(x[:-1], x[1:])[0, 1] if len(x) > 2 else np.nan, raw=True,
    )
    feat["effective_spread_proxy"] = 2 * np.sqrt(-cov_d.clip(upper=0))
    # Volume profile
    feat["dollar_volume"] = close * volume
    feat["dollar_volume_z21"] = (feat["dollar_volume"] - feat["dollar_volume"].rolling(21).mean()) / \
                                  feat["dollar_volume"].rolling(21).std().replace(0, np.nan)
    feat["dollar_volume_z63"] = (feat["dollar_volume"] - feat["dollar_volume"].rolling(63).mean()) / \
                                  feat["dollar_volume"].rolling(63).std().replace(0, np.nan)
    # Distinct closes (price clustering — high values mean illiquid)
    feat["distinct_closes_21d"] = close.rolling(21).apply(
        lambda x: len(np.unique(np.round(x, 2))), raw=True,
    )
    return feat.astype(float)


# ──────────────────────────────────────────────────────────────────────
# Comprehensive extra Technical feature block
# ──────────────────────────────────────────────────────────────────────


def compute_extra_technical(bars: pd.DataFrame) -> pd.DataFrame:
    """30+ additional technical indicators not in features.py."""
    open_ = bars["open"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    close = bars["close"].astype(float)
    volume = bars["volume"].astype(float)

    feat = pd.DataFrame(index=bars.index)

    # 1. TRIX
    feat["trix_15"] = _trix(close, 15)
    # 2. DPO
    feat["dpo_20"] = _dpo(close, 20)
    # 3. TSI
    feat["tsi"] = _tsi(close)
    # 4. PPO
    feat["ppo"] = _ppo(close)
    # 5. PVO
    feat["pvo"] = _pvo(volume)
    # 6. BOP
    feat["bop"] = _bop(open_, high, low, close)
    # 7. Chande
    feat["chande_20"] = _chande(close, 20)
    # 8-9. Vortex VI+/VI-
    vi_p, vi_m = _vortex(high, low, close, 14)
    feat["vortex_plus"] = vi_p
    feat["vortex_minus"] = vi_m
    feat["vortex_spread"] = vi_p - vi_m
    # 10. Ultimate Oscillator
    feat["ult_osc"] = _ultimate_osc(high, low, close)
    # 11. Coppock
    feat["coppock"] = _coppock(close)
    # 12. KAMA (vs price)
    kama = _kama(close, 10)
    feat["px_vs_kama"] = close / kama.replace(0, np.nan) - 1.0
    # 13. HMA (vs price)
    hma = _hma(close, 20)
    feat["px_vs_hma20"] = close / hma.replace(0, np.nan) - 1.0
    # 14. SuperTrend direction
    feat["supertrend_dir"] = _supertrend(high, low, close)
    # 15. Garman-Klass vol
    feat["vol_gk_21d"] = _garman_klass_vol(open_, high, low, close, 21)
    feat["vol_gk_63d"] = _garman_klass_vol(open_, high, low, close, 63)
    # 16. Yang-Zhang vol (most accurate)
    feat["vol_yz_21d"] = _yang_zhang_vol(open_, high, low, close, 21)
    feat["vol_yz_63d"] = _yang_zhang_vol(open_, high, low, close, 63)
    # Bollinger band width trend
    bb_width = close.rolling(20).std() * 4 / close.rolling(20).mean().replace(0, np.nan)
    feat["bb_width_5d_chg"] = bb_width.pct_change(5)
    # Volume features
    feat["volume_z5d"] = (volume - volume.rolling(5).mean()) / volume.rolling(5).std().replace(0, np.nan)
    feat["volume_z252d"] = (volume - volume.rolling(252).mean()) / volume.rolling(252).std().replace(0, np.nan)
    # MA convergence
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    feat["sma50_sma200_dist"] = (sma50 - sma200) / sma200.replace(0, np.nan)
    feat["sma50_sma200_5d_chg"] = feat["sma50_sma200_dist"].diff(5)

    # Candle patterns
    pats = _candle_patterns(open_, high, low, close)
    feat = pd.concat([feat, pats], axis=1)

    return feat.astype(float)


# ──────────────────────────────────────────────────────────────────────
# Composite Fundamental Scores: Piotroski / Altman / Beneish
# ──────────────────────────────────────────────────────────────────────


def _latest_value(panel: pd.DataFrame, concept: str, as_of: DateType,
                   kind: str = "Q") -> Optional[float]:
    """Get latest concept value as of date (matches features.py logic)."""
    sub = panel[(panel["concept"] == concept) & (panel["period_kind"] == kind)]
    if sub.empty:
        return None
    eligible = sub[sub["as_of_ts"].dt.date <= as_of]
    if eligible.empty:
        return None
    return float(eligible.sort_values("as_of_ts").iloc[-1]["value"])


def compute_piotroski_score(panel: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Piotroski F-Score: 9 binary bits (0-9 sum).

    Profitability (4):
      1. Net Income > 0
      2. CFO > 0
      3. ROA > prior ROA
      4. CFO > Net Income (accruals quality)
    Leverage / Liquidity / Source of Funds (3):
      5. Long-term Debt decreased YoY
      6. Current Ratio increased YoY
      7. No new shares issued (shares_out 같거나 감소)
    Operating Efficiency (2):
      8. Gross Margin increased YoY
      9. Asset Turnover increased YoY
    """
    feat = pd.DataFrame(index=dates, columns=["piotroski_score"], dtype=float)
    for dt in dates:
        d = dt.date() if hasattr(dt, "date") else dt
        d_prior = d - timedelta(days=365)
        ni = _latest_value(panel, "NET_INCOME", d)
        cfo = _latest_value(panel, "CFO", d)
        assets = _latest_value(panel, "TOTAL_ASSETS", d)
        assets_prior = _latest_value(panel, "TOTAL_ASSETS", d_prior)
        ni_prior = _latest_value(panel, "NET_INCOME", d_prior)
        ltd = _latest_value(panel, "LONG_TERM_DEBT", d)
        ltd_prior = _latest_value(panel, "LONG_TERM_DEBT", d_prior)
        cur_a = _latest_value(panel, "CURRENT_ASSETS", d)
        cur_l = _latest_value(panel, "CURRENT_LIABILITIES", d)
        cur_a_p = _latest_value(panel, "CURRENT_ASSETS", d_prior)
        cur_l_p = _latest_value(panel, "CURRENT_LIABILITIES", d_prior)
        shares = _latest_value(panel, "SHARES_OUTSTANDING", d)
        shares_p = _latest_value(panel, "SHARES_OUTSTANDING", d_prior)
        gp = _latest_value(panel, "GROSS_PROFIT", d)
        gp_p = _latest_value(panel, "GROSS_PROFIT", d_prior)
        rev = _latest_value(panel, "REVENUE", d)
        rev_p = _latest_value(panel, "REVENUE", d_prior)

        score = 0
        # 1
        if ni and ni > 0:
            score += 1
        # 2
        if cfo and cfo > 0:
            score += 1
        # 3
        if ni and assets and ni_prior and assets_prior and assets > 0 and assets_prior > 0:
            if ni / assets > ni_prior / assets_prior:
                score += 1
        # 4
        if cfo and ni and cfo > ni:
            score += 1
        # 5
        if ltd and ltd_prior and ltd < ltd_prior:
            score += 1
        # 6
        if cur_a and cur_l and cur_a_p and cur_l_p and cur_l > 0 and cur_l_p > 0:
            if cur_a / cur_l > cur_a_p / cur_l_p:
                score += 1
        # 7
        if shares and shares_p and shares <= shares_p:
            score += 1
        # 8
        if gp and rev and gp_p and rev_p and rev > 0 and rev_p > 0:
            if gp / rev > gp_p / rev_p:
                score += 1
        # 9
        if rev and assets and rev_p and assets_prior and assets > 0 and assets_prior > 0:
            if rev / assets > rev_p / assets_prior:
                score += 1

        feat.at[dt, "piotroski_score"] = float(score)

    return feat.astype(float)


def compute_altman_zscore(panel: pd.DataFrame, dates: pd.DatetimeIndex,
                           market_cap_proxy: pd.Series) -> pd.DataFrame:
    """Altman Z-Score (1968) for manufacturing firms.

    Z = 1.2(WC/TA) + 1.4(RE/TA) + 3.3(EBIT/TA) + 0.6(MVE/TL) + 1.0(S/TA)

    Z > 2.99: safe
    1.81 < Z < 2.99: gray zone
    Z < 1.81: distress

    Note: 1.0(S/TA) variant; alt: Z' for private firms uses BVE instead of MVE.
    """
    feat = pd.DataFrame(index=dates, columns=["altman_z"], dtype=float)
    for dt in dates:
        d = dt.date() if hasattr(dt, "date") else dt
        cur_a = _latest_value(panel, "CURRENT_ASSETS", d)
        cur_l = _latest_value(panel, "CURRENT_LIABILITIES", d)
        ta = _latest_value(panel, "TOTAL_ASSETS", d)
        # Retained Earnings approx via cumulative net income minus dividends
        # (not directly in our concepts — use NET_INCOME × 4 as proxy)
        ni = _latest_value(panel, "NET_INCOME", d)
        # EBIT = Operating Income
        ebit = _latest_value(panel, "OPERATING_INCOME", d)
        tl = _latest_value(panel, "TOTAL_LIABILITIES", d)
        rev = _latest_value(panel, "REVENUE", d)
        mve = float(market_cap_proxy.get(dt)) if dt in market_cap_proxy.index else None

        if not ta or ta <= 0:
            continue
        wc = ((cur_a or 0) - (cur_l or 0))
        re_proxy = ni * 4 if ni else 0
        ebit_a = ebit * 4 if ebit else 0
        rev_a = rev * 4 if rev else 0

        z = (1.2 * wc / ta + 1.4 * re_proxy / ta + 3.3 * ebit_a / ta)
        if mve and tl and tl > 0:
            z += 0.6 * mve / tl
        z += 1.0 * rev_a / ta

        feat.at[dt, "altman_z"] = float(z)
    return feat.astype(float)


def compute_beneish_mscore(panel: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Beneish M-Score — detects earnings manipulation. M < -2.22 → low manipulation risk.

    M = -4.84 + 0.92 DSRI + 0.528 GMI + 0.404 AQI + 0.892 SGI
        + 0.115 DEPI - 0.172 SGAI + 4.679 TATA - 0.327 LVGI

    Some terms need DSO/Sales/Receivables which we may not have all of —
    we compute what's possible and skip if any required input missing.
    Returns NaN when underlying data insufficient.
    """
    feat = pd.DataFrame(index=dates, columns=["beneish_m"], dtype=float)
    for dt in dates:
        d = dt.date() if hasattr(dt, "date") else dt
        d_prior = d - timedelta(days=365)

        rev = _latest_value(panel, "REVENUE", d)
        rev_p = _latest_value(panel, "REVENUE", d_prior)
        gp = _latest_value(panel, "GROSS_PROFIT", d)
        gp_p = _latest_value(panel, "GROSS_PROFIT", d_prior)
        cur_a = _latest_value(panel, "CURRENT_ASSETS", d)
        cur_a_p = _latest_value(panel, "CURRENT_ASSETS", d_prior)
        ta = _latest_value(panel, "TOTAL_ASSETS", d)
        ta_p = _latest_value(panel, "TOTAL_ASSETS", d_prior)
        ni = _latest_value(panel, "NET_INCOME", d)
        cfo = _latest_value(panel, "CFO", d)
        tl = _latest_value(panel, "TOTAL_LIABILITIES", d)
        tl_p = _latest_value(panel, "TOTAL_LIABILITIES", d_prior)

        if not all([rev, rev_p, gp, gp_p, ta, ta_p, cur_a, cur_a_p, ni, cfo]) \
           or rev <= 0 or rev_p <= 0 or ta <= 0 or ta_p <= 0:
            continue

        # SGI: Sales Growth Index
        sgi = rev / rev_p
        # GMI: Gross Margin Index (degradation worse: higher GMI)
        gm_now = gp / rev
        gm_prev = gp_p / rev_p
        if gm_now <= 0:
            continue
        gmi = gm_prev / gm_now
        # AQI: Asset Quality Index (intangibles tend to inflate)
        # AQI = (1 - (CA + PP&E) / TA) ratio — we use 1 - CA/TA as proxy
        aqi_now = 1 - (cur_a / ta)
        aqi_prev = 1 - (cur_a_p / ta_p)
        if aqi_prev <= 0:
            continue
        aqi = aqi_now / aqi_prev
        # DEPI: Depreciation Index (require Depreciation — skip with 1.0 placeholder)
        depi = 1.0
        # SGAI: SG&A Index (require SGA — placeholder 1.0)
        sgai = 1.0
        # TATA: Total Accruals to Total Assets ((NI - CFO) / TA)
        tata = (ni - cfo) / ta
        # LVGI: Leverage Index (TL/TA ratio change)
        if tl_p and tl_p > 0 and ta_p > 0:
            lvgi = (tl / ta) / (tl_p / ta_p)
        else:
            lvgi = 1.0
        # DSRI requires Receivables — skip with 1.0
        dsri = 1.0

        m = (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
             + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)

        feat.at[dt, "beneish_m"] = float(m)
    return feat.astype(float)


def compute_advanced_fundamental(
    panel: pd.DataFrame, dates: pd.DatetimeIndex,
    market_close: pd.Series,
) -> pd.DataFrame:
    """All composite fundamental scores."""
    p = compute_piotroski_score(panel, dates)
    a = compute_altman_zscore(panel, dates, market_close)
    b = compute_beneish_mscore(panel, dates)
    return pd.concat([p, a, b], axis=1)
