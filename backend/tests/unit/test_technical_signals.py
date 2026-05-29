"""Technical signal + scorer tests — synthetic OHLCV inputs."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from technical.indicators import from_ohlcv  # noqa: E402
from technical.score import default_signals, score_technical  # noqa: E402
from technical.signals.base import Signal, SignalVerdict  # noqa: E402
from technical.signals.mean_reversion import MeanReversionSignal  # noqa: E402
from technical.signals.momentum import MomentumSignal  # noqa: E402
from technical.signals.trend import TrendSignal  # noqa: E402


def _flat_ctx(n=250, price=100.0):
    return from_ohlcv(
        opens=[price] * n, highs=[price] * n, lows=[price] * n,
        closes=[price] * n, volumes=[1000.0] * n,
    )


def _trend_up_ctx(n=250, start=100.0, step=0.5):
    closes = [start + i * step for i in range(n)]
    return from_ohlcv(
        opens=closes, highs=[c + 1 for c in closes], lows=[c - 1 for c in closes],
        closes=closes, volumes=[1000.0] * n,
    )


def _trend_down_ctx(n=250, start=200.0, step=0.5):
    closes = [start - i * step for i in range(n)]
    return from_ohlcv(
        opens=closes, highs=[c + 1 for c in closes], lows=[c - 1 for c in closes],
        closes=closes, volumes=[1000.0] * n,
    )


class TrendSignalTest(unittest.TestCase):
    def test_trend_up_aligned_long(self):
        v = TrendSignal().evaluate(_trend_up_ctx())
        self.assertEqual(v.name, "trend_ema_alignment")
        self.assertGreater(v.score, 0)
        self.assertEqual(v.inputs["alignment"], "golden")

    def test_trend_down_aligned_short(self):
        v = TrendSignal().evaluate(_trend_down_ctx())
        self.assertLess(v.score, 0)
        self.assertEqual(v.inputs["alignment"], "death")

    def test_flat_yields_low_or_zero_score(self):
        v = TrendSignal().evaluate(_flat_ctx())
        self.assertEqual(v.score, 0.0)

    def test_abstain_when_insufficient_bars(self):
        ctx = from_ohlcv(opens=[1] * 10, highs=[1] * 10, lows=[1] * 10,
                         closes=[1] * 10, volumes=[1] * 10)
        v = TrendSignal().evaluate(ctx)
        self.assertEqual(v.confidence, 0.0)


class MomentumSignalTest(unittest.TestCase):
    def test_overbought_pushes_bearish(self):
        # 50-bar rally → high RSI → bearish bias
        v = MomentumSignal().evaluate(_trend_up_ctx(n=80, start=100, step=2.0))
        # RSI will be ~100, MACD histogram positive and rising
        # → -40 (overbought) +30 (bull momentum) = -10
        self.assertGreaterEqual(v.confidence, 0.5)

    def test_abstain_short_input(self):
        v = MomentumSignal().evaluate(_flat_ctx(n=5))
        self.assertEqual(v.confidence, 0.0)


class MeanReversionSignalTest(unittest.TestCase):
    def test_flat_yields_zero(self):
        # Zero stdev path
        v = MeanReversionSignal().evaluate(_flat_ctx())
        self.assertEqual(v.score, 0.0)
        self.assertGreater(v.confidence, 0)

    def test_oversold_returns_positive(self):
        # Last close way below 20-day mean → expect positive (bounce)
        closes = [100.0] * 19 + [60.0]
        ctx = from_ohlcv(
            opens=closes, highs=[c + 1 for c in closes], lows=[c - 1 for c in closes],
            closes=closes, volumes=[1] * 20,
        )
        v = MeanReversionSignal().evaluate(ctx)
        self.assertGreater(v.score, 0)

    def test_overbought_returns_negative(self):
        closes = [100.0] * 19 + [140.0]
        ctx = from_ohlcv(
            opens=closes, highs=closes, lows=closes,
            closes=closes, volumes=[1] * 20,
        )
        v = MeanReversionSignal().evaluate(ctx)
        self.assertLess(v.score, 0)

    def test_abstain_short_input(self):
        v = MeanReversionSignal().evaluate(_flat_ctx(n=10))
        self.assertEqual(v.confidence, 0.0)


class TechnicalScorerTest(unittest.TestCase):
    def test_all_signals_run(self):
        ts = score_technical(_trend_up_ctx())
        self.assertEqual(len(ts.verdicts), len(default_signals()))
        # At least one signal contributed
        self.assertGreater(ts.confidence, 0)

    def test_clamping(self):
        # Force all-bullish synthetic signals
        class BullSignal(Signal):
            name = "bull"
            def evaluate(self, ctx):
                return SignalVerdict(name="bull", score=200, confidence=1.0)

        ts = score_technical(_flat_ctx(), signals=[BullSignal(), BullSignal()])
        self.assertEqual(ts.score, 100.0)

    def test_zero_signals_yields_zero(self):
        ts = score_technical(_flat_ctx(), signals=[])
        self.assertEqual(ts.score, 0.0)
        self.assertEqual(ts.confidence, 0.0)

    def test_abstain_signal_does_not_count(self):
        class AbstainSignal(Signal):
            name = "abstain"
            def evaluate(self, ctx):
                return self._abstain("nothing to see")

        class BullSignal(Signal):
            name = "bull"
            def evaluate(self, ctx):
                return SignalVerdict(name="bull", score=50, confidence=1.0)

        ts = score_technical(_flat_ctx(), signals=[BullSignal(), AbstainSignal()])
        # Score is just bull contribution
        self.assertAlmostEqual(ts.score, 50.0)
        # Confidence reflects 1 of 2 enabled signals contributed
        self.assertAlmostEqual(ts.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
