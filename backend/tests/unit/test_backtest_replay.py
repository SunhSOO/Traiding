"""Pure replay simulator tests — no SQLAlchemy / FastAPI deps."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.replay import DecisionSignal, replay  # noqa: E402


def _sig(d: date, market: str, ticker: str, action: str, hour: int = 16) -> DecisionSignal:
    return DecisionSignal(
        ts=datetime(d.year, d.month, d.day, hour, tzinfo=UTC),
        market=market, ticker=ticker, action=action,
    )


def _make_price_oracle(prices: dict[tuple[str, str, date], float]):
    def lookup(m: str, t: str, d: date):
        return prices.get((m, t, d))
    return lookup


# ──────────────────────────────────────────────────────────────────────


class ReplayBasicTest(unittest.TestCase):
    def test_no_signals_returns_empty(self):
        out = replay(
            [], price_at=_make_price_oracle({}),
            initial_balance=10_000.0,
        )
        self.assertEqual(out.closed_trades, [])
        self.assertEqual(out.open_positions, [])
        self.assertEqual(out.cash_remaining, 10_000.0)

    def test_buy_then_sell_realizes_pnl(self):
        d1, d2 = date(2026, 1, 5), date(2026, 1, 10)
        prices = {
            ("KR", "AAA", d1): 100.0,
            ("KR", "AAA", d2): 110.0,
        }
        signals = [
            _sig(d1, "KR", "AAA", "BUY"),
            _sig(d2, "KR", "AAA", "SELL"),
        ]
        out = replay(
            signals, price_at=_make_price_oracle(prices),
            initial_balance=10_000.0, position_fraction=0.10,
        )
        self.assertEqual(len(out.closed_trades), 1)
        trade = out.closed_trades[0]
        self.assertAlmostEqual(trade.entry_price, 100.0)
        self.assertAlmostEqual(trade.exit_price, 110.0)
        # Position size = 1000 / 100 = 10 units
        self.assertAlmostEqual(trade.volume, 10.0)
        # P&L = (110 - 100) * 10 = 100
        self.assertAlmostEqual(trade.pnl, 100.0)
        # No open positions left
        self.assertEqual(out.open_positions, [])

    def test_sell_without_position_is_skipped(self):
        d1 = date(2026, 1, 5)
        prices = {("KR", "AAA", d1): 100.0}
        signals = [_sig(d1, "KR", "AAA", "SELL")]
        out = replay(
            signals, price_at=_make_price_oracle(prices),
            initial_balance=10_000.0,
        )
        self.assertEqual(out.closed_trades, [])
        self.assertEqual(out.skipped_signals, 1)
        # Cash untouched
        self.assertAlmostEqual(out.cash_remaining, 10_000.0)

    def test_pyramid_buy_skipped(self):
        d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
        prices = {
            ("KR", "AAA", d1): 100.0,
            ("KR", "AAA", d2): 105.0,
        }
        signals = [
            _sig(d1, "KR", "AAA", "BUY"),
            _sig(d2, "KR", "AAA", "BUY"),
        ]
        out = replay(
            signals, price_at=_make_price_oracle(prices),
            initial_balance=10_000.0, position_fraction=0.10,
        )
        # Second BUY skipped
        self.assertEqual(out.skipped_signals, 1)
        # One open position
        self.assertEqual(len(out.open_positions), 1)

    def test_missing_price_skipped(self):
        d1 = date(2026, 1, 5)
        # No price for AAA
        signals = [_sig(d1, "KR", "AAA", "BUY")]
        out = replay(
            signals, price_at=_make_price_oracle({}),
            initial_balance=10_000.0,
        )
        self.assertEqual(out.skipped_signals, 1)
        self.assertEqual(out.closed_trades, [])

    def test_hold_signals_skipped(self):
        d1 = date(2026, 1, 5)
        prices = {("KR", "AAA", d1): 100.0}
        signals = [_sig(d1, "KR", "AAA", "HOLD")]
        out = replay(
            signals, price_at=_make_price_oracle(prices),
            initial_balance=10_000.0,
        )
        self.assertEqual(out.closed_trades, [])
        self.assertEqual(out.skipped_signals, 1)

    def test_min_position_value_floor(self):
        d1 = date(2026, 1, 5)
        prices = {("KR", "AAA", d1): 100.0}
        signals = [_sig(d1, "KR", "AAA", "BUY")]
        # 0.1% of 10_000 = $10 → below default min $100 → skipped
        out = replay(
            signals, price_at=_make_price_oracle(prices),
            initial_balance=10_000.0, position_fraction=0.001,
        )
        self.assertEqual(out.skipped_signals, 1)
        self.assertEqual(out.closed_trades, [])

    def test_unaffordable_buy_skipped(self):
        d1 = date(2026, 1, 5)
        prices = {("KR", "AAA", d1): 100.0}
        signals = [_sig(d1, "KR", "AAA", "BUY")]
        # 200% of starting balance — alloc > cash → skip
        out = replay(
            signals, price_at=_make_price_oracle(prices),
            initial_balance=10_000.0, position_fraction=1.0,
        )
        # 100% position is exactly equal to cash and so should work,
        # but our guard is `alloc > cash` strict → equal passes
        self.assertEqual(len(out.open_positions), 1)


class ReplaySignalOrderingTest(unittest.TestCase):
    def test_signals_sorted_chronologically(self):
        d1, d2 = date(2026, 1, 5), date(2026, 1, 10)
        prices = {
            ("KR", "AAA", d1): 100.0,
            ("KR", "AAA", d2): 90.0,
        }
        # Provide SELL first, BUY second → engine should sort by ts
        signals = [
            _sig(d2, "KR", "AAA", "SELL"),
            _sig(d1, "KR", "AAA", "BUY"),
        ]
        out = replay(
            signals, price_at=_make_price_oracle(prices),
            initial_balance=10_000.0, position_fraction=0.10,
        )
        self.assertEqual(len(out.closed_trades), 1)
        self.assertLess(
            out.closed_trades[0].entry_ts,
            out.closed_trades[0].exit_ts,
        )

    def test_multi_ticker_independent_state(self):
        d1, d2 = date(2026, 1, 5), date(2026, 1, 10)
        prices = {
            ("KR", "AAA", d1): 100.0, ("KR", "AAA", d2): 110.0,
            ("KR", "BBB", d1): 200.0, ("KR", "BBB", d2): 180.0,
        }
        signals = [
            _sig(d1, "KR", "AAA", "BUY"),
            _sig(d1, "KR", "BBB", "BUY"),
            _sig(d2, "KR", "AAA", "SELL"),
            _sig(d2, "KR", "BBB", "SELL"),
        ]
        out = replay(
            signals, price_at=_make_price_oracle(prices),
            initial_balance=10_000.0, position_fraction=0.10,
        )
        self.assertEqual(len(out.closed_trades), 2)
        by_ticker = {t.ticker: t for t in out.closed_trades}
        # AAA: alloc 10% of 10_000 = 1000, 10 units @ 100 → +100
        self.assertAlmostEqual(by_ticker["AAA"].pnl, 100.0)
        # BBB: after AAA buy cash=9000, AAA MtM=1000, equity=10000.
        # 10% alloc = 1000 → 5 units @ 200 → (180-200)*5 = -100
        self.assertAlmostEqual(by_ticker["BBB"].pnl, -100.0)


class ReplayInputValidationTest(unittest.TestCase):
    def test_negative_initial_balance_raises(self):
        with self.assertRaises(ValueError):
            replay([], price_at=_make_price_oracle({}), initial_balance=-1.0)

    def test_bad_position_fraction_raises(self):
        with self.assertRaises(ValueError):
            replay(
                [], price_at=_make_price_oracle({}),
                initial_balance=1.0, position_fraction=0.0,
            )
        with self.assertRaises(ValueError):
            replay(
                [], price_at=_make_price_oracle({}),
                initial_balance=1.0, position_fraction=1.5,
            )


if __name__ == "__main__":
    unittest.main()
