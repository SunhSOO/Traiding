"""Mean-reversion signal — z-score of close vs 20-day SMA.

z = (close - SMA20) / stdev(close, 20).

- z > +2 → -50 (stretched up, expect pullback)
- z > +1 → -25
- z < -2 → +50 (oversold, expect bounce)
- z < -1 → +25
- |z| < 1 → 0

Confidence is 1.0 when we have ≥ 20 closes, else 0.
"""
from __future__ import annotations

from math import sqrt

from technical.signals.base import Signal, SignalVerdict


class MeanReversionSignal(Signal):
    name = "mean_reversion_zscore"

    def evaluate(self, ctx) -> SignalVerdict:
        if len(ctx.closes) < 20:
            return self._abstain("need ≥ 20 closes for SMA(20)")

        window = ctx.closes[-20:]
        mean = sum(window) / 20
        variance = sum((x - mean) ** 2 for x in window) / 20
        stdev = sqrt(variance) if variance > 0 else 0.0
        close_now = ctx.closes[-1]

        if stdev == 0:
            return SignalVerdict(
                name=self.name, score=0.0, confidence=0.5,
                inputs={"zscore": 0.0, "note": "zero stdev"},
            )

        z = (close_now - mean) / stdev
        if z > 2:
            score = -50.0
        elif z > 1:
            score = -25.0
        elif z < -2:
            score = 50.0
        elif z < -1:
            score = 25.0
        else:
            score = 0.0

        return SignalVerdict(
            name=self.name, score=score, confidence=1.0,
            inputs={"zscore": z, "mean_sma20": mean, "stdev_sma20": stdev, "close": close_now},
        )
