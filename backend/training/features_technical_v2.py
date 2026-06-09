"""Wave 2 — Comprehensive Technical features.

Adds ~35 new technical signals:

  - Ichimoku (8): tenkan, kijun, senkou_a, senkou_b, chikou_dist,
                  cloud_thickness, px_vs_kumo, kumo_twist_dir
  - Divergences (4): rsi_div, macd_div, obv_div, hidden_div
  - TTM Squeeze (3): ttm_squeeze, ttm_squeeze_duration, ttm_squeeze_dir
  - Pivot Points (6): pivot_std, r1_dist, s1_dist, fib_pivot,
                      camarilla_h3_dist, camarilla_l3_dist
  - Order Flow Proxies (5): amihud_illiquidity, kyle_lambda, roll_spread,
                            uptick_volume_ratio, vpoc_dist_proxy
  - Indicator Acceleration (5): rsi_5d_chg, macd_hist_5d_chg, adx_5d_chg,
                                bb_pctb_5d_chg, volume_z_5d_chg
  - S/R Proximity (4): dist_to_52w_high, dist_to_52w_low,
                        round_number_dist, tests_at_resistance_21d
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────────────────
# Ichimoku
# ──────────────────────────────────────────────────────────────────────


def compute_ichimoku(bars: pd.DataFrame) -> pd.DataFrame:
    high, low, close = bars["high"], bars["low"], bars["close"]
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    chikou = close.shift(-26)
    cloud_thick = (senkou_a - senkou_b)
    px_vs_kumo = np.where(
        close > senkou_a, 1.0,
        np.where(close < senkou_b, -1.0, 0.0),
    )
    twist = (senkou_a > senkou_b).astype(int).diff().fillna(0)

    out = pd.DataFrame({
        "ichimoku_tenkan_dist": (close - tenkan) / close,
        "ichimoku_kijun_dist": (close - kijun) / close,
        "ichimoku_senkou_a_dist": (close - senkou_a) / close,
        "ichimoku_senkou_b_dist": (close - senkou_b) / close,
        "ichimoku_chikou_dist": (close - chikou.shift(26)) / close,
        "ichimoku_cloud_thick": cloud_thick / close,
        "ichimoku_px_vs_kumo": px_vs_kumo,
        "ichimoku_kumo_twist": twist,
    }, index=bars.index)
    return out


# ──────────────────────────────────────────────────────────────────────
# Divergences (RSI / MACD / OBV)
# ──────────────────────────────────────────────────────────────────────


def _detect_divergence(price: pd.Series, indicator: pd.Series, window: int = 21) -> pd.Series:
    """Detect bullish (+1) / bearish (-1) divergence on rolling local extrema."""
    out = pd.Series(0.0, index=price.index)
    for i in range(window * 2, len(price)):
        win_p = price.iloc[i - window: i]
        win_i = indicator.iloc[i - window: i]
        if win_p.isna().any() or win_i.isna().any():
            continue
        p_min_idx, p_max_idx = win_p.idxmin(), win_p.idxmax()
        i_min_idx, i_max_idx = win_i.idxmin(), win_i.idxmax()
        # Bullish: price lower low, indicator higher low
        if (win_p.iloc[-1] < win_p.iloc[0]) and (win_i.iloc[-1] > win_i.iloc[0]):
            out.iloc[i] = 1.0
        # Bearish: price higher high, indicator lower high
        elif (win_p.iloc[-1] > win_p.iloc[0]) and (win_i.iloc[-1] < win_i.iloc[0]):
            out.iloc[i] = -1.0
    return out


def compute_divergences(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["close"]
    # RSI
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    down = (-delta).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)

    # MACD hist
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - sig

    # OBV
    vol = bars["volume"]
    obv = (np.sign(close.diff()).fillna(0) * vol).cumsum()

    out = pd.DataFrame(index=bars.index)
    out["rsi_divergence"] = _detect_divergence(close, rsi)
    out["macd_divergence"] = _detect_divergence(close, macd_hist)
    out["obv_divergence"] = _detect_divergence(close, obv)
    # Hidden divergence (price higher low + indicator lower low = bullish continuation)
    hidden = pd.Series(0.0, index=close.index)
    for i in range(42, len(close)):
        p_win = close.iloc[i - 21: i]
        r_win = rsi.iloc[i - 21: i]
        if p_win.isna().any() or r_win.isna().any():
            continue
        if (p_win.iloc[-1] > p_win.iloc[0]) and (r_win.iloc[-1] < r_win.iloc[0]):
            hidden.iloc[i] = 1.0
        elif (p_win.iloc[-1] < p_win.iloc[0]) and (r_win.iloc[-1] > r_win.iloc[0]):
            hidden.iloc[i] = -1.0
    out["hidden_divergence"] = hidden
    return out


# ──────────────────────────────────────────────────────────────────────
# TTM Squeeze (Bollinger inside Keltner)
# ──────────────────────────────────────────────────────────────────────


def compute_ttm_squeeze(bars: pd.DataFrame) -> pd.DataFrame:
    high, low, close = bars["high"], bars["low"], bars["close"]
    ma = close.rolling(20).mean()
    sd = close.rolling(20).std()
    bb_upper = ma + 2 * sd
    bb_lower = ma - 2 * sd
    tr = pd.concat([
        (high - low),
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(20).mean()
    kc_upper = ma + 1.5 * atr
    kc_lower = ma - 1.5 * atr

    in_squeeze = ((bb_upper < kc_upper) & (bb_lower > kc_lower)).astype(int)

    # Duration of current squeeze
    duration = pd.Series(0, index=bars.index)
    streak = 0
    for i, v in enumerate(in_squeeze.values):
        streak = streak + 1 if v == 1 else 0
        duration.iat[i] = streak

    # Squeeze fire direction (when squeeze ends, momentum direction)
    momentum = close - close.shift(20)
    fire_dir = pd.Series(0.0, index=bars.index)
    in_sq = in_squeeze.values
    mom = momentum.values
    for i in range(1, len(in_sq)):
        if in_sq[i - 1] == 1 and in_sq[i] == 0:
            fire_dir.iat[i] = 1.0 if mom[i] > 0 else -1.0

    return pd.DataFrame({
        "ttm_squeeze_on": in_squeeze,
        "ttm_squeeze_duration": duration,
        "ttm_squeeze_fire": fire_dir,
    }, index=bars.index)


# ──────────────────────────────────────────────────────────────────────
# Pivot Points
# ──────────────────────────────────────────────────────────────────────


def compute_pivots(bars: pd.DataFrame) -> pd.DataFrame:
    high, low, close = bars["high"].shift(1), bars["low"].shift(1), bars["close"].shift(1)
    pivot = (high + low + close) / 3
    r1 = 2 * pivot - low
    s1 = 2 * pivot - high
    # Fibonacci
    rng = high - low
    fib_r1 = pivot + 0.382 * rng
    fib_s1 = pivot - 0.382 * rng
    # Camarilla H3/L3 (가장 신뢰성 높은 레벨)
    cam_h3 = close + rng * 1.1 / 4
    cam_l3 = close - rng * 1.1 / 4

    c = bars["close"]
    return pd.DataFrame({
        "pivot_std_dist": (c - pivot) / c,
        "pivot_r1_dist": (c - r1) / c,
        "pivot_s1_dist": (c - s1) / c,
        "pivot_fib_r1_dist": (c - fib_r1) / c,
        "pivot_cam_h3_dist": (c - cam_h3) / c,
        "pivot_cam_l3_dist": (c - cam_l3) / c,
    }, index=bars.index)


# ──────────────────────────────────────────────────────────────────────
# Order-flow proxies
# ──────────────────────────────────────────────────────────────────────


def compute_order_flow_proxies(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["close"]
    ret = close.pct_change().fillna(0)
    vol = bars["volume"]
    dvol = close * vol

    # Amihud illiquidity = avg(|ret| / dollar_volume)
    amihud = (ret.abs() / dvol.replace(0, np.nan)).rolling(21).mean() * 1e6

    # Kyle's lambda — slope of |ret| vs signed dvol
    signed_dv = dvol * np.sign(ret)
    rolling_kyle = pd.Series(np.nan, index=bars.index)
    for i in range(21, len(bars)):
        win_ret = ret.iloc[i - 21: i]
        win_sdv = signed_dv.iloc[i - 21: i]
        if win_sdv.std() > 0:
            cov = np.cov(win_ret, win_sdv)[0, 1]
            var = win_sdv.var()
            rolling_kyle.iat[i] = (cov / var) if var > 0 else np.nan

    # Roll's spread estimator = 2 * sqrt(-cov(ΔP_t, ΔP_t-1)) when cov < 0
    dprice = close.diff()
    roll = pd.Series(np.nan, index=bars.index)
    for i in range(21, len(bars)):
        win = dprice.iloc[i - 21: i].dropna()
        if len(win) > 5:
            cov = np.cov(win.iloc[1:], win.iloc[:-1])[0, 1]
            if cov < 0:
                roll.iat[i] = 2 * np.sqrt(-cov) / close.iat[i]

    # Uptick volume ratio = sum(vol when close>prev_close) / total vol
    uptick_v = (vol * (ret > 0).astype(float)).rolling(21).sum()
    total_v = vol.rolling(21).sum()
    uptick_ratio = uptick_v / total_v

    # VPOC distance proxy = (close - rolling_vwap_21) / atr_14
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    vwap_21 = (typical * vol).rolling(21).sum() / vol.rolling(21).sum()
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - bars["close"].shift(1)).abs(),
        (bars["low"] - bars["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    vpoc_dist = (close - vwap_21) / atr14.replace(0, np.nan)

    return pd.DataFrame({
        "amihud_illiquidity": amihud,
        "kyle_lambda": rolling_kyle,
        "roll_spread": roll,
        "uptick_volume_ratio": uptick_ratio,
        "vpoc_dist_proxy": vpoc_dist,
    }, index=bars.index)


# ──────────────────────────────────────────────────────────────────────
# Indicator acceleration (5d change of selected indicators)
# ──────────────────────────────────────────────────────────────────────


def compute_indicator_accel(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["close"]
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    down = (-delta).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_sig = macd.ewm(span=9, adjust=False).mean()
    macd_h = macd - macd_sig
    ma = close.rolling(20).mean()
    sd = close.rolling(20).std()
    pctb = (close - (ma - 2 * sd)) / (4 * sd.replace(0, np.nan))
    vol_z = (bars["volume"] - bars["volume"].rolling(21).mean()) / bars["volume"].rolling(21).std()
    # ADX simplified
    high = bars["high"]; low = bars["low"]
    up_move = high.diff()
    dn_move = -low.diff()
    plus_dm = ((up_move > dn_move) & (up_move > 0)) * up_move
    minus_dm = ((dn_move > up_move) & (dn_move > 0)) * dn_move
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    plus_di = 100 * plus_dm.rolling(14).sum() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.rolling(14).sum() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(14).mean()

    return pd.DataFrame({
        "rsi_5d_chg": rsi - rsi.shift(5),
        "macd_hist_5d_chg": macd_h - macd_h.shift(5),
        "adx_5d_chg": adx - adx.shift(5),
        "bb_pctb_5d_chg": pctb - pctb.shift(5),
        "volume_z_5d_chg": vol_z - vol_z.shift(5),
    }, index=bars.index)


# ──────────────────────────────────────────────────────────────────────
# Support/Resistance + round number proximity
# ──────────────────────────────────────────────────────────────────────


def compute_sr_features(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["close"]
    high_52w = bars["high"].rolling(252).max()
    low_52w = bars["low"].rolling(252).min()
    # Round-number proximity — nearest power-of-10 grid (1, 5, 10, 25, 50, 100 multiples)
    grid_levels = []
    for c in close:
        if not np.isfinite(c):
            grid_levels.append(np.nan); continue
        magnitudes = [1, 2.5, 5, 10, 25, 50, 100]
        scale = 10 ** int(np.floor(np.log10(abs(c)))) if c > 0 else 1
        candidates = [m * scale for m in magnitudes]
        candidates += [m * scale / 10 for m in magnitudes]
        nearest = min(candidates, key=lambda x: abs(x - c))
        grid_levels.append((c - nearest) / c)
    round_dist = pd.Series(grid_levels, index=bars.index)

    # Tests at 21d high/low
    high_21 = bars["high"].rolling(21).max()
    tests_h = ((bars["high"] >= high_21 * 0.99).rolling(21).sum()).fillna(0)

    return pd.DataFrame({
        "dist_to_52w_high": (close - high_52w) / close,
        "dist_to_52w_low": (close - low_52w) / close,
        "round_number_dist": round_dist,
        "tests_at_resistance_21d": tests_h,
    }, index=bars.index)


# ──────────────────────────────────────────────────────────────────────
# Top-level
# ──────────────────────────────────────────────────────────────────────


def compute_technical_v2(bars: pd.DataFrame) -> pd.DataFrame:
    parts = [
        compute_ichimoku(bars),
        compute_divergences(bars),
        compute_ttm_squeeze(bars),
        compute_pivots(bars),
        compute_order_flow_proxies(bars),
        compute_indicator_accel(bars),
        compute_sr_features(bars),
    ]
    return pd.concat(parts, axis=1)
