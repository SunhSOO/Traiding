"""Paper-broker persistence tests with mocked SQLAlchemy session.

Real DB integration deferred to tests/integration/test_paper_db.py.
"""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import sqlalchemy  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from brokers.base import OrderIntent, OrderSide, OrderType  # noqa: E402
    from brokers.paper import PaperBrokerLogic, Quote  # noqa: E402
    from brokers.paper_persistence import (  # noqa: E402
        load_or_create_account,
        persist_close,
        persist_open,
        rehydrate_logic,
    )
    from core.models.paper import PaperAccount, PaperPosition  # noqa: E402
    from core.types import Market  # noqa: E402


def make_intent(volume=10):
    return OrderIntent(
        market=Market.KR, ticker="005930", side=OrderSide.BUY,
        order_type=OrderType.MARKET, volume=volume,
    )


def make_quote(bid=70_000.0, ask=70_010.0):
    return Quote(bid=bid, ask=ask, last=70_005.0, ts=datetime.now(UTC))


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class LoadOrCreateAccountTest(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()

    def test_creates_when_missing(self):
        self.session.scalars.return_value.first.return_value = None
        acc = load_or_create_account(
            self.session, name="default", base_currency="KRW", initial_balance=10_000_000
        )
        self.session.add.assert_called_once()
        self.assertEqual(acc.name, "default")
        self.assertEqual(acc.base_currency, "KRW")

    def test_returns_existing(self):
        existing = PaperAccount(
            id=1, name="default", base_currency="KRW",
            initial_balance=10_000_000, current_balance=9_500_000,
        )
        self.session.scalars.return_value.first.return_value = existing
        acc = load_or_create_account(
            self.session, name="default", base_currency="KRW", initial_balance=10_000_000
        )
        self.session.add.assert_not_called()
        self.assertIs(acc, existing)


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class RehydrateLogicTest(unittest.TestCase):
    def test_rehydrate_with_no_positions(self):
        session = MagicMock()
        account = PaperAccount(
            id=1, name="default", base_currency="KRW",
            initial_balance=10_000_000, current_balance=10_000_000,
        )
        session.scalars.return_value = iter([])
        logic = rehydrate_logic(session, account)
        self.assertEqual(logic.cash, 10_000_000)
        self.assertEqual(len(logic.open_positions), 0)

    def test_rehydrate_restores_open_positions(self):
        session = MagicMock()
        account = PaperAccount(
            id=1, name="default", base_currency="KRW",
            initial_balance=10_000_000, current_balance=9_200_000,
        )
        pos_row = PaperPosition(
            account_id=1, market="KR", ticker="005930", side="BUY",
            volume=10, entry_price=70_000, entry_ts=datetime(2026, 5, 26, tzinfo=UTC),
        )
        session.scalars.return_value = iter([pos_row])
        logic = rehydrate_logic(session, account)
        self.assertEqual(logic.cash, 9_200_000)
        self.assertEqual(len(logic.open_positions), 1)
        pos = logic.open_positions[0]
        self.assertEqual(pos.market, Market.KR)
        self.assertEqual(pos.ticker, "005930")
        self.assertEqual(pos.volume, 10)


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class PersistOpenCloseTest(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.account = PaperAccount(
            id=1, name="default", base_currency="KRW",
            initial_balance=10_000_000, current_balance=10_000_000,
        )

    def test_persist_open_inserts_position_and_updates_cash(self):
        logic = PaperBrokerLogic(base_currency="KRW", initial_balance=10_000_000)
        intent = make_intent()
        result, pos = logic.open(intent, make_quote(), now=datetime(2026, 5, 26, tzinfo=UTC))
        self.assertTrue(result.ok)
        # Now persist
        persist_open(self.session, self.account, pos, result, new_cash=logic.cash)
        # Account cash adjusted
        self.assertEqual(self.account.current_balance, logic.cash)
        # One row added (the PaperPosition)
        self.session.add.assert_called_once()
        added = self.session.add.call_args[0][0]
        self.assertEqual(added.ticker, "005930")
        self.assertEqual(added.account_id, 1)

    def test_persist_close_deletes_position_and_writes_trade(self):
        logic = PaperBrokerLogic(base_currency="KRW", initial_balance=10_000_000)
        intent = make_intent()
        now = datetime(2026, 5, 26, tzinfo=UTC)
        logic.open(intent, make_quote(70_000, 70_010), now=now)
        result, trade = logic.close(Market.KR, "005930", make_quote(72_000, 72_010), now=now)
        self.assertTrue(result.ok)

        persist_close(self.session, self.account, trade, result, new_cash=logic.cash)
        # Should have executed a DELETE and added a PaperTrade row
        self.session.execute.assert_called_once()
        self.session.add.assert_called_once()
        added = self.session.add.call_args[0][0]
        self.assertEqual(added.ticker, "005930")
        self.assertGreater(added.pnl, 0)
        self.assertEqual(self.account.current_balance, logic.cash)


if __name__ == "__main__":
    unittest.main()
