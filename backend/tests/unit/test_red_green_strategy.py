import unittest
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from technical.signals.red_green import Candle, RedGreenStrategy


def make_candles(count=220):
    base = datetime(2026, 1, 1)
    price = 100.0
    candles = []
    for i in range(count):
        price += 0.12 if i < count // 2 else -0.04
        candles.append(
            Candle(
                time=base + timedelta(minutes=15 * i),
                open=price - 0.2,
                high=price + 0.8,
                low=price - 0.7,
                close=price,
                volume=100 + i,
            )
        )
    return candles


class RedGreenStrategyTest(unittest.TestCase):
    def test_calculates_indicator_after_lookback(self):
        strategy = RedGreenStrategy()
        decision = strategy.on_candles(make_candles())

        self.assertIsNotNone(decision.indicator)
        self.assertTrue(decision.conditions["ready"])
        self.assertIn(decision.indicator.trend, {-1, 1})

    def test_same_bar_is_not_processed_twice(self):
        strategy = RedGreenStrategy()
        candles = make_candles()

        first = strategy.on_candles(candles)
        second = strategy.on_candles(candles)

        self.assertTrue(first.conditions["ready"])
        self.assertTrue(second.conditions["duplicate_bar"])


if __name__ == "__main__":
    unittest.main()
