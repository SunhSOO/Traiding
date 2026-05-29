"""Red-Green signal adapter.

Wraps the existing :class:`RedGreenStrategy` (BB-ICHI cloud +
Supertrend + state machine) into the unified Signal interface. The
strategy's verdict on the latest bar — entered long, entered short,
holding, idle — is mapped to a score in [-100, +100].

We don't run the strategy's full state-machine for this adapter
(positions / SL trailing / etc.) — that's the runtime layer's job.
We just call ``calculate`` to get the latest IndicatorPoint and read
``trend`` + ``cloud color`` to decide score.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from technical.signals.base import Signal, SignalVerdict
from technical.signals.red_green import Candle, RedGreenConfig, RedGreenStrategy


class RedGreenSignal(Signal):
    """Use Red-Green indicator only (no position state) as one signal."""

    name = "red_green"

    def __init__(self, config: Optional[RedGreenConfig] = None):
        self._strategy = RedGreenStrategy(config or RedGreenConfig())

    def evaluate(self, ctx) -> SignalVerdict:
        candles = _to_candles(ctx)
        if len(candles) < max(self._strategy.config.long_period,
                              self._strategy.config.mid_period) + 2:
            return self._abstain("insufficient bars for BB-ICHI lookback")
        points = self._strategy.calculate(candles)
        latest = points[-1]
        if latest is None:
            return self._abstain("indicator not yet warmed up")

        cloud_green = latest.upperline1 >= latest.upperline2
        # Score combinations: trend × cloud
        if latest.trend == 1 and cloud_green:
            score = 60.0
            kind = "bull_aligned"
        elif latest.trend == -1 and not cloud_green:
            score = -60.0
            kind = "bear_aligned"
        elif latest.trend == 1:
            score = 20.0
            kind = "trend_up_only"
        elif latest.trend == -1:
            score = -20.0
            kind = "trend_down_only"
        else:
            score = 0.0
            kind = "neutral"

        return SignalVerdict(
            name=self.name, score=score, confidence=1.0,
            inputs={
                "trend": latest.trend, "cloud_green": cloud_green,
                "close": latest.close, "kizun": latest.kizun,
                "alignment": kind,
            },
        )


def _to_candles(ctx) -> list[Candle]:
    """IndicatorContext lists → Red-Green Candle list. The Red-Green
    strategy uses datetime per bar; we synthesise sequential daily
    timestamps because the signal only cares about ORDER, not absolute
    time."""
    base = datetime(2000, 1, 1)
    from datetime import timedelta

    return [
        Candle(
            time=base + timedelta(days=i),
            open=ctx.opens[i], high=ctx.highs[i],
            low=ctx.lows[i], close=ctx.closes[i],
            volume=ctx.volumes[i],
        )
        for i in range(len(ctx.closes))
    ]
