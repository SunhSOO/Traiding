"""Momentum signal — RSI + MACD histogram.

Logic (deterministic, tested):
- RSI(14) above 70 → -40 (overbought, bearish bias)
- RSI(14) below 30 → +40 (oversold, bullish bias)
- MACD histogram > 0 and rising → +30
- MACD histogram < 0 and falling → -30
- Final score is the sum, clipped to [-100, +100].

Confidence:
- 0 when not enough data
- 0.4 when only RSI defined
- 0.6 when only MACD defined
- 1.0 when both contribute
"""
from __future__ import annotations

from technical.signals.base import Signal, SignalVerdict


class MomentumSignal(Signal):
    name = "momentum_rsi_macd"

    def evaluate(self, ctx) -> SignalVerdict:
        rsi_series = ctx.rsi(14)
        macd_data = ctx.macd()
        rsi_now = rsi_series[-1] if rsi_series and rsi_series[-1] is not None else None
        hist = macd_data["histogram"]
        hist_now = hist[-1] if hist and hist[-1] is not None else None
        hist_prev = hist[-2] if len(hist) >= 2 and hist[-2] is not None else None

        if rsi_now is None and hist_now is None:
            return self._abstain("insufficient bars")

        score = 0.0
        contributions: list[str] = []
        if rsi_now is not None:
            if rsi_now > 70:
                score -= 40
                contributions.append("rsi_overbought")
            elif rsi_now < 30:
                score += 40
                contributions.append("rsi_oversold")
        if hist_now is not None and hist_prev is not None:
            rising = hist_now > hist_prev
            if hist_now > 0 and rising:
                score += 30
                contributions.append("macd_bull_momentum")
            elif hist_now < 0 and not rising:
                score -= 30
                contributions.append("macd_bear_momentum")

        score = max(-100.0, min(100.0, score))

        # Confidence weight: 0.4 per contributing source, capped to 1.0
        sources = (rsi_now is not None) + (hist_now is not None and hist_prev is not None)
        confidence = {0: 0.0, 1: 0.5, 2: 1.0}[sources]

        return SignalVerdict(
            name=self.name,
            score=score,
            confidence=confidence,
            inputs={
                "rsi": rsi_now,
                "macd_histogram": hist_now,
                "macd_histogram_prev": hist_prev,
                "contributions": contributions,
            },
        )
