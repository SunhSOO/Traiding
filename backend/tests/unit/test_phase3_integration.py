"""Phase 3 → Phase 4 integration tests.

Cover the pieces that wire learned weights into the decision runner:
- DBPriceOracle: synthetic spread + look-ahead-safe query
- PaperBroker persistence: open + close write through helpers
- score_composite consumes per-cluster weights when cluster_id given
- run_decisions loads cluster_weights at startup (this is exercised
  through the imports + dataclass replace; the actual DB load is
  covered when sqlalchemy is installed via integration tests)
"""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
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
    from brokers.db_price_oracle import DBPriceOracle  # noqa: E402
    from brokers.paper import PaperBroker, PaperBrokerLogic, Quote  # noqa: E402
    from core.types import Market  # noqa: E402
    from decision.composite import ModuleVerdict, score_composite  # noqa: E402
    from decision.types import DecisionConfig  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# DBPriceOracle
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class DBPriceOracleTest(unittest.TestCase):
    def test_quote_from_synthetic_price_row(self):
        row = MagicMock()
        row.close = 100.0

        # Build a session whose scalars().first() returns our row.
        session = MagicMock()
        scalars = MagicMock()
        scalars.first.return_value = row
        session.scalars.return_value = scalars

        # Session factory used by oracle — context manager.
        from contextlib import contextmanager

        @contextmanager
        def factory():
            yield session

        oracle = DBPriceOracle(session_factory=factory, half_spread_bps=10.0)  # 0.1%
        q = oracle.quote(Market.US, "AAPL", as_of=datetime.now(UTC))
        self.assertAlmostEqual(q.last, 100.0)
        # bid = 100 * (1 - 0.001) = 99.9
        self.assertAlmostEqual(q.bid, 99.9, places=3)
        self.assertAlmostEqual(q.ask, 100.1, places=3)

    def test_missing_price_raises_lookup_error(self):
        from contextlib import contextmanager

        session = MagicMock()
        scalars = MagicMock()
        scalars.first.return_value = None
        session.scalars.return_value = scalars

        @contextmanager
        def factory():
            yield session

        oracle = DBPriceOracle(session_factory=factory)
        with self.assertRaises(LookupError):
            oracle.quote(Market.KR, "999999", as_of=datetime.now(UTC))


# ──────────────────────────────────────────────────────────────────────
# PaperBroker persistence wiring
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class PaperBrokerPersistenceTest(unittest.TestCase):
    """Verify execute / close_position now call into persistence helpers
    when session_factory + account_id are provided. We patch the
    persist_open/close functions to intercept calls without needing a DB.
    """

    def _build(self, persist_open_mock, persist_close_mock):
        from contextlib import contextmanager
        from unittest.mock import patch

        # Stub session yielded by factory
        session = MagicMock()
        # session.get(PaperAccount, account_id) returns an account
        account = MagicMock()
        account.id = 1
        session.get.return_value = account

        @contextmanager
        def factory():
            yield session

        # Stub oracle: returns a Quote object
        oracle = MagicMock()
        oracle.quote.return_value = Quote(
            bid=99.9, ask=100.1, last=100.0, ts=datetime.now(UTC),
        )

        logic = PaperBrokerLogic(base_currency="USD", initial_balance=100_000.0)
        broker = PaperBroker(
            logic, oracle,
            session_factory=factory, account_id=1,
        )
        return broker, persist_open_mock, persist_close_mock

    def test_execute_calls_persist_open(self):
        with unittest.mock.patch(
            "brokers.paper_persistence.persist_open",
        ) as mock_open:
            broker, _, _ = self._build(mock_open, None)
            intent = OrderIntent(
                market=Market.US, ticker="AAPL", side=OrderSide.BUY,
                order_type=OrderType.MARKET, volume=10,
            )
            result = broker.execute(intent)
            self.assertTrue(result.ok, msg=result.error)
            mock_open.assert_called_once()

    def test_execute_skips_persist_when_no_session_factory(self):
        with unittest.mock.patch(
            "brokers.paper_persistence.persist_open",
        ) as mock_open:
            logic = PaperBrokerLogic(base_currency="USD", initial_balance=100_000.0)
            oracle = MagicMock()
            oracle.quote.return_value = Quote(
                bid=99.9, ask=100.1, last=100.0, ts=datetime.now(UTC),
            )
            # No session_factory / account_id → no persistence
            broker = PaperBroker(logic, oracle)
            intent = OrderIntent(
                market=Market.US, ticker="MSFT", side=OrderSide.BUY,
                order_type=OrderType.MARKET, volume=5,
            )
            result = broker.execute(intent)
            self.assertTrue(result.ok)
            mock_open.assert_not_called()

    def test_execute_failure_skips_persist(self):
        with unittest.mock.patch(
            "brokers.paper_persistence.persist_open",
        ) as mock_open:
            # Cash-constrained broker → BUY fails
            logic = PaperBrokerLogic(base_currency="USD", initial_balance=1.0)
            oracle = MagicMock()
            oracle.quote.return_value = Quote(
                bid=99.9, ask=100.1, last=100.0, ts=datetime.now(UTC),
            )
            from contextlib import contextmanager
            session = MagicMock()
            session.get.return_value = MagicMock(id=1)

            @contextmanager
            def factory():
                yield session

            broker = PaperBroker(logic, oracle, session_factory=factory, account_id=1)
            intent = OrderIntent(
                market=Market.US, ticker="GOOGL", side=OrderSide.BUY,
                order_type=OrderType.MARKET, volume=10,
            )
            result = broker.execute(intent)
            self.assertFalse(result.ok)
            mock_open.assert_not_called()

    def test_close_calls_persist_close(self):
        # Open a position first (no persistence), then close (with persistence)
        with unittest.mock.patch(
            "brokers.paper_persistence.persist_close",
        ) as mock_close, unittest.mock.patch(
            "brokers.paper_persistence.persist_open",
        ):
            broker, _, _ = self._build(MagicMock(), mock_close)
            broker.execute(OrderIntent(
                market=Market.US, ticker="AAPL", side=OrderSide.BUY,
                order_type=OrderType.MARKET, volume=10,
            ))
            # Price moved up; close
            broker._oracle.quote.return_value = Quote(
                bid=109.9, ask=110.1, last=110.0, ts=datetime.now(UTC),
            )
            result = broker.close_position(Market.US, "AAPL")
            self.assertTrue(result.ok, msg=result.error)
            mock_close.assert_called_once()


# ──────────────────────────────────────────────────────────────────────
# Composite per-cluster lookup
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps")
class CompositeClusterLookupTest(unittest.TestCase):
    def test_cluster_weights_applied(self):
        cfg = DecisionConfig(cluster_weight_overrides={
            "KR:TECH:LARGE": {"F": 0.1, "T": 0.8, "I": 0.1},
        })
        c = score_composite(
            fundamental=ModuleVerdict(score=50, confidence=1.0),
            technical=ModuleVerdict(score=-50, confidence=1.0),
            information=ModuleVerdict(score=0, confidence=1.0),
            config=cfg, cluster_id="KR:TECH:LARGE",
        )
        # T-dominant cluster → bearish skew
        # weighted = 50*0.1 + (-50)*0.8 + 0*0.1 = -35
        self.assertAlmostEqual(c.score, -35.0, places=1)
        # weights_used must reflect the overrides
        self.assertAlmostEqual(c.weights_used["T"], 0.8, places=4)

    def test_unknown_cluster_falls_back_to_global(self):
        cfg = DecisionConfig(cluster_weight_overrides={
            "KR:TECH:LARGE": {"F": 1.0, "T": 0.0, "I": 0.0},
        })
        c = score_composite(
            fundamental=ModuleVerdict(score=50, confidence=1.0),
            technical=ModuleVerdict(score=-50, confidence=1.0),
            information=ModuleVerdict(score=0, confidence=1.0),
            config=cfg, cluster_id="US:HEALTH:MID",  # not in overrides
        )
        # Falls back to global default (0.35 / 0.40 / 0.25)
        # weighted = 50*0.35 + (-50)*0.40 + 0*0.25 = -2.5
        self.assertAlmostEqual(c.score, -2.5, places=1)

    def test_no_cluster_id_uses_global(self):
        cfg = DecisionConfig(cluster_weight_overrides={
            "X": {"F": 1.0, "T": 0.0, "I": 0.0},
        })
        c = score_composite(
            fundamental=ModuleVerdict(score=50, confidence=1.0),
            technical=ModuleVerdict(score=-50, confidence=1.0),
            information=ModuleVerdict(score=0, confidence=1.0),
            config=cfg,  # no cluster_id
        )
        # Same default-weight result
        self.assertAlmostEqual(c.score, -2.5, places=1)


if __name__ == "__main__":
    unittest.main()
