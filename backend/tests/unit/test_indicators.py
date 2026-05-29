"""Pure-Python indicator implementation tests (used as fallback when
pandas-ta is unavailable, and as the production path inside Signal
classes that don't need vectorisation)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from technical.indicators import _ema, _macd, _rsi, _sma, from_ohlcv  # noqa: E402


class SmaEmaTest(unittest.TestCase):
    def test_sma_basic(self):
        # SMA(3) of [1..10] → first defined at index 2 = 2.0; last = 9.0
        out = _sma([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 3)
        self.assertIsNone(out[0])
        self.assertIsNone(out[1])
        self.assertAlmostEqual(out[2], 2.0)
        self.assertAlmostEqual(out[9], 9.0)

    def test_sma_short_input(self):
        self.assertEqual(_sma([1, 2], 5), [None, None])

    def test_ema_basic(self):
        out = _ema([1.0] * 20, 5)
        # EMA of constant series stabilises at the constant
        self.assertAlmostEqual(out[-1], 1.0)

    def test_ema_short_input(self):
        self.assertEqual(_ema([1, 2], 5), [None, None])


class RsiTest(unittest.TestCase):
    def test_constant_series_is_100_when_no_losses(self):
        # All-up series → all gains, no losses → RSI = 100
        out = _rsi([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16], 14)
        # First 14 are None (warm-up), then 100
        self.assertIsNone(out[13])
        self.assertAlmostEqual(out[14], 100.0)

    def test_short_input(self):
        out = _rsi([1, 2, 3], 14)
        self.assertEqual(out, [None, None, None])


class MacdTest(unittest.TestCase):
    def test_keys_present(self):
        # Trending series
        out = _macd(list(range(1, 60)))
        self.assertIn("macd", out)
        self.assertIn("signal", out)
        self.assertIn("histogram", out)
        # MACD line should be positive after warm-up (trend up)
        non_null = [v for v in out["macd"] if v is not None]
        self.assertTrue(non_null[-1] > 0)


class IndicatorContextTest(unittest.TestCase):
    def test_from_ohlcv_validates_length(self):
        with self.assertRaises(ValueError):
            from_ohlcv(
                opens=[1, 2], highs=[1, 2, 3], lows=[1, 2],
                closes=[1, 2], volumes=[1, 2],
            )

    def test_cache_returns_same_object(self):
        ctx = from_ohlcv(
            opens=[1.0] * 30, highs=[1.0] * 30, lows=[1.0] * 30,
            closes=[1.0] * 30, volumes=[1.0] * 30,
        )
        a = ctx.sma(5)
        b = ctx.sma(5)
        self.assertIs(a, b)


if __name__ == "__main__":
    unittest.main()
