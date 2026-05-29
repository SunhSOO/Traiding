"""Trend signal — EMA alignment.

Three EMAs: 20, 50, 200.
- Golden alignment (close > EMA20 > EMA50 > EMA200) → +70
- Death alignment (close < EMA20 < EMA50 < EMA200) → -70
- Partial alignment → ±30
- No alignment → 0

Confidence is 1.0 when all three EMAs are defined, 0.5 when only the
short two are, 0 when not enough bars (we need >= 200 closes).
"""
from __future__ import annotations

from technical.signals.base import Signal, SignalVerdict


class TrendSignal(Signal):
    name = "trend_ema_alignment"

    def evaluate(self, ctx) -> SignalVerdict:
        e20 = _last(ctx.ema(20))
        e50 = _last(ctx.ema(50))
        e200 = _last(ctx.ema(200))
        close_now = ctx.closes[-1] if ctx.closes else None

        if close_now is None or e20 is None or e50 is None:
            return self._abstain("insufficient bars for EMA(20)/EMA(50)")

        if e200 is not None and close_now > e20 > e50 > e200:
            score = 70.0
            kind = "golden"
        elif e200 is not None and close_now < e20 < e50 < e200:
            score = -70.0
            kind = "death"
        elif close_now > e20 > e50:
            score = 30.0
            kind = "partial_bull"
        elif close_now < e20 < e50:
            score = -30.0
            kind = "partial_bear"
        else:
            score = 0.0
            kind = "mixed"

        confidence = 1.0 if e200 is not None else 0.5
        return SignalVerdict(
            name=self.name, score=score, confidence=confidence,
            inputs={
                "close": close_now, "ema20": e20, "ema50": e50, "ema200": e200,
                "alignment": kind,
            },
        )


def _last(series):
    return series[-1] if series and series[-1] is not None else None
