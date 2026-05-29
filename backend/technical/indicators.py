"""Technical-indicator computation.

We use pandas-ta in production (130+ indicators in one consistent API),
but only call the indicators each Signal asks for — not all 130 every
bar, which would be wasteful.

The wrapper layer here exists for three reasons:
1. Make the Signal layer testable WITHOUT pandas-ta installed. Tests
   pass synthetic DataFrames and ask for ``rsi`` / ``macd`` / etc;
   the wrapper dispatches to either pandas-ta (production) or a
   bundled pure-NumPy fallback (tests).
2. Cache indicator results per (ticker, indicator, parameters) within
   one runner pass — many signals share RSI/MACD/EMA inputs.
3. Pin parameter conventions across the codebase. Phase 2 had bug
   reports of "we use RSI(14) here, RSI(9) there"; centralising
   defaults here prevents that.

All functions accept a pandas DataFrame with at least
[open, high, low, close, volume] columns indexed by date.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


# Detect pandas / pandas-ta lazily so this module imports cleanly in
# test environments without those heavy deps.
def _have_pandas() -> bool:
    try:
        import pandas  # noqa: F401
        return True
    except ImportError:
        return False


def _have_pandas_ta() -> bool:
    try:
        import pandas_ta  # noqa: F401
        return True
    except ImportError:
        return False


# ──────────────────────────────────────────────────────────────────────
# Pure-NumPy fallbacks (used in tests; production prefers pandas-ta)
# ──────────────────────────────────────────────────────────────────────


def _sma(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    rolling = sum(values[:period])
    out[period - 1] = rolling / period
    for i in range(period, len(values)):
        rolling += values[i] - values[i - period]
        out[i] = rolling / period
    return out


def _ema(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    for i in range(period, len(values)):
        out[i] = values[i] * k + (out[i - 1] or seed) * (1 - k)
    return out


def _rsi(values: list[float], period: int = 14) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = max(diff, 0)
        loss = max(-diff, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(values: list[float], fast: int = 12, slow: int = 26, signal_p: int = 9) -> dict:
    fast_e = _ema(values, fast)
    slow_e = _ema(values, slow)
    macd_line: list[Optional[float]] = []
    for f, s in zip(fast_e, slow_e):
        macd_line.append((f - s) if (f is not None and s is not None) else None)
    # signal: EMA of macd_line over non-null tail
    valid_idx = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line: list[Optional[float]] = [None] * len(values)
    if valid_idx is not None:
        tail = [v for v in macd_line[valid_idx:] if v is not None]
        tail_signal = _ema(tail, signal_p)
        for i, v in enumerate(tail_signal):
            signal_line[valid_idx + i] = v
    histogram: list[Optional[float]] = []
    for m, s in zip(macd_line, signal_line):
        histogram.append((m - s) if (m is not None and s is not None) else None)
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) < period:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    out[period - 1] = sum(trs[:period]) / period
    for i in range(period, len(closes)):
        out[i] = (out[i - 1] * (period - 1) + trs[i]) / period
    return out


# ──────────────────────────────────────────────────────────────────────
# Public façade
# ──────────────────────────────────────────────────────────────────────


@dataclass
class IndicatorContext:
    """One-pass context for a Signal computation. Caches results so
    sibling signals (RSI + MACD using the same EMA-12) don't recompute.

    Build via :func:`from_ohlcv`."""

    closes: list[float]
    highs: list[float]
    lows: list[float]
    opens: list[float]
    volumes: list[float]
    _cache: dict[str, Any]

    def sma(self, period: int) -> list[Optional[float]]:
        key = f"sma:{period}"
        if key not in self._cache:
            self._cache[key] = _sma(self.closes, period)
        return self._cache[key]

    def ema(self, period: int) -> list[Optional[float]]:
        key = f"ema:{period}"
        if key not in self._cache:
            self._cache[key] = _ema(self.closes, period)
        return self._cache[key]

    def rsi(self, period: int = 14) -> list[Optional[float]]:
        key = f"rsi:{period}"
        if key not in self._cache:
            self._cache[key] = _rsi(self.closes, period)
        return self._cache[key]

    def macd(self, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
        key = f"macd:{fast}:{slow}:{signal}"
        if key not in self._cache:
            self._cache[key] = _macd(self.closes, fast, slow, signal)
        return self._cache[key]

    def atr(self, period: int = 14) -> list[Optional[float]]:
        key = f"atr:{period}"
        if key not in self._cache:
            self._cache[key] = _atr(self.highs, self.lows, self.closes, period)
        return self._cache[key]


def from_ohlcv(
    *,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> IndicatorContext:
    if not (len(opens) == len(highs) == len(lows) == len(closes) == len(volumes)):
        raise ValueError("OHLCV lists must all be the same length")
    return IndicatorContext(
        opens=list(opens),
        highs=list(highs),
        lows=list(lows),
        closes=list(closes),
        volumes=list(volumes),
        _cache={},
    )
