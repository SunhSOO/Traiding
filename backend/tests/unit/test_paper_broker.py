"""Pure-logic tests for PaperBrokerLogic.

DB-touching paths (PaperBroker.execute persisting to paper_positions)
are integration tests, deferred until PostgreSQL is reachable.
"""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.types import Market  # noqa: E402
from brokers.base import OrderIntent, OrderSide, OrderType  # noqa: E402
from brokers.paper import PaperBrokerLogic, Quote  # noqa: E402


def make_kr_intent(side="BUY", volume=10, ticker="005930", sl=None, tp=None):
    return OrderIntent(
        market=Market.KR, ticker=ticker, side=OrderSide(side),
        order_type=OrderType.MARKET, volume=volume, sl=sl, tp=tp,
    )


def make_quote(bid=70_000.0, ask=70_010.0, last=70_005.0):
    return Quote(bid=bid, ask=ask, last=last, ts=datetime.now(UTC))


class PaperBrokerLogicTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 5, 26, 6, 30, tzinfo=UTC)
        self.broker = PaperBrokerLogic(
            base_currency="KRW",
            initial_balance=10_000_000,  # 10M KRW
            slippage_bps=1.0,
        )

    # ── Open ──
    def test_open_buy_succeeds_and_consumes_cash(self):
        result, pos = self.broker.open(make_kr_intent("BUY", volume=10), make_quote(), now=self.now)
        self.assertTrue(result.ok, msg=result.error)
        self.assertIsNotNone(pos)
        self.assertEqual(pos.market, Market.KR)
        self.assertEqual(pos.side, OrderSide.BUY)
        # Buy fills at ask * (1 + 1bps) = 70010 * 1.0001 = 70017.001
        self.assertAlmostEqual(result.fill_price, 70_010.0 * 1.0001, places=2)
        # Cash should drop by gross + costs
        self.assertLess(self.broker.cash, 10_000_000)

    def test_open_sell_does_not_require_cash(self):
        broker = PaperBrokerLogic(base_currency="KRW", initial_balance=0)
        result, _ = broker.open(make_kr_intent("SELL", volume=10), make_quote(), now=self.now)
        self.assertTrue(result.ok, msg=result.error)

    def test_open_buy_rejects_when_insufficient_cash(self):
        # 10 shares * 70k ≈ 700k, but cash is only 100k
        broker = PaperBrokerLogic(base_currency="KRW", initial_balance=100_000)
        result, _ = broker.open(make_kr_intent("BUY", volume=10), make_quote(), now=self.now)
        self.assertFalse(result.ok)
        self.assertIn("Insufficient cash", result.error or "")

    def test_open_rejects_non_market_order(self):
        intent = OrderIntent(
            market=Market.KR, ticker="005930",
            side=OrderSide.BUY, order_type=OrderType.LIMIT, volume=10,
            limit_price=70_000.0,
        )
        result, _ = self.broker.open(intent, make_quote(), now=self.now)
        self.assertFalse(result.ok)
        self.assertIn("MARKET", result.error or "")

    def test_open_rejects_duplicate_position(self):
        self.broker.open(make_kr_intent("BUY"), make_quote(), now=self.now)
        result, _ = self.broker.open(make_kr_intent("BUY"), make_quote(), now=self.now)
        self.assertFalse(result.ok)
        self.assertIn("Already have open position", result.error or "")

    # ── Close ──
    def test_close_returns_proceeds(self):
        self.broker.open(make_kr_intent("BUY", volume=10), make_quote(70_000, 70_010), now=self.now)
        cash_after_open = self.broker.cash
        # Price moves up
        result, trade = self.broker.close(
            Market.KR, "005930",
            quote=make_quote(70_500, 70_510),
            now=self.now,
        )
        self.assertTrue(result.ok, msg=result.error)
        self.assertIsNotNone(trade)
        self.assertGreater(trade.pnl, 0)  # bought at ~70k, sold at ~70.5k
        # Cash should rise above post-open level
        self.assertGreater(self.broker.cash, cash_after_open)

    def test_close_long_with_loss(self):
        self.broker.open(make_kr_intent("BUY"), make_quote(70_000, 70_010), now=self.now)
        result, trade = self.broker.close(
            Market.KR, "005930", quote=make_quote(68_000, 68_010), now=self.now,
        )
        self.assertTrue(result.ok)
        self.assertLess(trade.pnl, 0)

    def test_close_unknown_position_fails(self):
        result, _ = self.broker.close(Market.KR, "999999", quote=make_quote(), now=self.now)
        self.assertFalse(result.ok)
        self.assertIn("No open position", result.error or "")

    def test_close_short_position_profitable(self):
        broker = PaperBrokerLogic(base_currency="KRW", initial_balance=0)
        broker.open(make_kr_intent("SELL", volume=10), make_quote(70_000, 70_010), now=self.now)
        # Price drops → short profitable
        result, trade = broker.close(Market.KR, "005930", quote=make_quote(68_000, 68_010), now=self.now)
        self.assertTrue(result.ok)
        self.assertGreater(trade.pnl, 0)

    # ── KR tax actually applied ──
    def test_kr_sell_tax_is_charged(self):
        self.broker.open(make_kr_intent("BUY", volume=100), make_quote(), now=self.now)
        result, trade = self.broker.close(
            Market.KR, "005930", quote=make_quote(70_500, 70_510), now=self.now,
        )
        # KR sell side adds 0.15% transaction tax on gross.
        self.assertGreater(trade.tax, 0)

    # ── Mark to market ──
    def test_mtm_returns_cash_when_no_positions(self):
        self.assertEqual(
            self.broker.mark_to_market(lambda m, t: make_quote()),
            self.broker.cash,
        )

    def test_mtm_includes_unrealized(self):
        self.broker.open(make_kr_intent("BUY", volume=10), make_quote(70_000, 70_010), now=self.now)
        equity = self.broker.mark_to_market(lambda m, t: make_quote(71_000, 71_010))
        self.assertGreater(equity, self.broker.cash)  # unrealised gain pushes equity above cash


class PaperBrokerLogicUSTest(unittest.TestCase):
    """Same broker, but operating on a US position to ensure the
    adapter dispatch works end-to-end."""

    def test_us_buy_then_sell_no_tax_on_buy(self):
        broker = PaperBrokerLogic(base_currency="USD", initial_balance=100_000)
        now = datetime(2026, 5, 26, 14, 30, tzinfo=UTC)
        intent = OrderIntent(
            market=Market.US, ticker="AAPL",
            side=OrderSide.BUY, order_type=OrderType.MARKET, volume=10,
        )
        result, _ = broker.open(intent, make_quote(190.0, 190.1, 190.05), now=now)
        self.assertTrue(result.ok, msg=result.error)
        # US has $0 commission default, no tx tax on buy.
        self.assertEqual(result.commission, 0.0)
        self.assertEqual(result.tax, 0.0)

        # Close — SEC fee should appear in tax
        result_close, trade = broker.close(Market.US, "AAPL", make_quote(192.0, 192.1, 192.05), now=now)
        self.assertTrue(result_close.ok)
        self.assertGreater(trade.tax, 0)  # SEC fee on sell


if __name__ == "__main__":
    unittest.main()
