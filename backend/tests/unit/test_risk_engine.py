"""Risk engine — every limit, every failure mode."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from brokers.base import OrderIntent, OrderSide, OrderType  # noqa: E402
from core.risk import RiskEngine, RiskLimits, RiskState  # noqa: E402
from core.types import Market  # noqa: E402


def make_intent(volume=10, ticker="005930", market=Market.KR) -> OrderIntent:
    return OrderIntent(
        market=market, ticker=ticker, side=OrderSide.BUY,
        order_type=OrderType.MARKET, volume=volume,
    )


def make_state(
    open_position_count=0,
    daily_pnl=0.0,
    consecutive_losses=0,
    current_spread_bps=5.0,
) -> RiskState:
    return RiskState(
        open_position_count=open_position_count,
        daily_pnl=daily_pnl,
        consecutive_losses=consecutive_losses,
        current_spread_bps=current_spread_bps,
    )


class RiskEngineHappyPathTest(unittest.TestCase):
    def setUp(self):
        self.engine = RiskEngine()
        self.limits = RiskLimits()  # defaults

    def test_clean_intent_passes_all_limits(self):
        result = self.engine.check(make_intent(), make_state(), self.limits)
        self.assertTrue(result.all_passed, msg=[f.reason for f in result.failures])
        self.assertEqual(len(result.failures), 0)
        # All individual flags should be True.
        for flag in (
            result.max_lot_pass, result.daily_loss_pass,
            result.consecutive_loss_pass, result.max_positions_pass,
            result.spread_pass, result.symbol_allowed_pass,
        ):
            self.assertTrue(flag)


class RiskEngineEachLimitTest(unittest.TestCase):
    def setUp(self):
        self.engine = RiskEngine()
        self.limits = RiskLimits(
            max_lot=10.0,
            daily_loss_limit=500.0,
            consecutive_loss_limit=3,
            max_positions=5,
            max_spread_bps=20.0,
        )

    def test_max_lot_fail(self):
        result = self.engine.check(make_intent(volume=20), make_state(), self.limits)
        self.assertFalse(result.all_passed)
        self.assertFalse(result.max_lot_pass)
        names = [f.limit_name for f in result.failures]
        self.assertIn("max_lot", names)

    def test_daily_loss_fail_when_loss_exceeds_limit(self):
        result = self.engine.check(make_intent(), make_state(daily_pnl=-500.01), self.limits)
        self.assertFalse(result.daily_loss_pass)

    def test_daily_loss_pass_when_within_limit(self):
        result = self.engine.check(make_intent(), make_state(daily_pnl=-499.99), self.limits)
        self.assertTrue(result.daily_loss_pass)

    def test_daily_pnl_positive_always_passes(self):
        result = self.engine.check(make_intent(), make_state(daily_pnl=10_000), self.limits)
        self.assertTrue(result.daily_loss_pass)

    def test_consecutive_loss_fail_at_limit(self):
        result = self.engine.check(make_intent(), make_state(consecutive_losses=3), self.limits)
        self.assertFalse(result.consecutive_loss_pass)

    def test_consecutive_loss_pass_below_limit(self):
        result = self.engine.check(make_intent(), make_state(consecutive_losses=2), self.limits)
        self.assertTrue(result.consecutive_loss_pass)

    def test_max_positions_fail_at_limit(self):
        result = self.engine.check(make_intent(), make_state(open_position_count=5), self.limits)
        self.assertFalse(result.max_positions_pass)

    def test_max_positions_pass_below_limit(self):
        result = self.engine.check(make_intent(), make_state(open_position_count=4), self.limits)
        self.assertTrue(result.max_positions_pass)

    def test_spread_fail_above_limit(self):
        result = self.engine.check(make_intent(), make_state(current_spread_bps=25.0), self.limits)
        self.assertFalse(result.spread_pass)

    def test_spread_unknown_fails_closed(self):
        result = self.engine.check(make_intent(), make_state(current_spread_bps=None), self.limits)
        self.assertFalse(result.spread_pass)
        names = [f.limit_name for f in result.failures]
        self.assertIn("spread", names)

    def test_symbol_allowlist_empty_means_no_restriction(self):
        result = self.engine.check(make_intent(ticker="005930"), make_state(), self.limits)
        self.assertTrue(result.symbol_allowed_pass)

    def test_symbol_allowlist_enforced_when_populated(self):
        limits = RiskLimits(
            symbol_allowlist_kr=frozenset({"005930", "000660"}),
        )
        # Allowed
        r1 = self.engine.check(make_intent(ticker="005930"), make_state(), limits)
        self.assertTrue(r1.symbol_allowed_pass)
        # Blocked
        r2 = self.engine.check(make_intent(ticker="666666"), make_state(), limits)
        self.assertFalse(r2.symbol_allowed_pass)

    def test_us_allowlist_independent_from_kr(self):
        limits = RiskLimits(
            symbol_allowlist_kr=frozenset({"005930"}),
            symbol_allowlist_us=frozenset({"AAPL"}),
        )
        r_kr = self.engine.check(make_intent(ticker="005930", market=Market.KR), make_state(), limits)
        r_us = self.engine.check(make_intent(ticker="AAPL", market=Market.US), make_state(), limits)
        self.assertTrue(r_kr.symbol_allowed_pass)
        self.assertTrue(r_us.symbol_allowed_pass)
        # Cross
        r_cross = self.engine.check(make_intent(ticker="AAPL", market=Market.KR), make_state(), limits)
        self.assertFalse(r_cross.symbol_allowed_pass)


class RiskEngineMultipleFailuresTest(unittest.TestCase):
    def test_all_six_can_fail_at_once(self):
        engine = RiskEngine()
        limits = RiskLimits(
            max_lot=1.0,
            daily_loss_limit=10.0,
            consecutive_loss_limit=1,
            max_positions=1,
            max_spread_bps=1.0,
            symbol_allowlist_kr=frozenset({"000001"}),
        )
        intent = make_intent(volume=999, ticker="666666")
        state = make_state(
            open_position_count=99,
            daily_pnl=-1_000,
            consecutive_losses=99,
            current_spread_bps=999,
        )
        result = engine.check(intent, state, limits)
        self.assertFalse(result.all_passed)
        self.assertEqual(len(result.failures), 6)


if __name__ == "__main__":
    unittest.main()
