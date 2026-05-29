"""Audit helper tests — uses MagicMock sessions so PostgreSQL is
not required. The persistence integration tests (real session vs
real schema) are in tests/integration/test_audit_db.py (to be
added next session after `alembic upgrade head` runs).
"""
from __future__ import annotations

import sys
import unittest
import uuid
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
    from core.audit import (  # noqa: E402
        DecisionRecord,
        ModuleScore,
        record_decision,
        record_decision_with_risk,
        record_risk_snapshot,
    )
    from core.risk import RiskCheckResult, RiskEngine, RiskLimits, RiskState  # noqa: E402
    from core.types import Market  # noqa: E402


def make_intent():
    return OrderIntent(
        market=Market.KR, ticker="005930", side=OrderSide.BUY,
        order_type=OrderType.MARKET, volume=10,
    )


def make_risk_result(passing: bool = True):
    engine = RiskEngine()
    intent = make_intent()
    state = RiskState(
        open_position_count=0, daily_pnl=0, consecutive_losses=0,
        current_spread_bps=5.0,
    )
    limits = RiskLimits(
        max_lot=1.0 if not passing else 100.0,  # 1.0 makes max_lot fail
    )
    return engine.check(intent, state, limits)


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed (run `uv sync`)")
class DecisionRecordValidationTest(unittest.TestCase):
    def test_default_decision_ts_is_set(self):
        rec = DecisionRecord(market=Market.KR, ticker="005930", action="HOLD")
        self.assertIsNotNone(rec.decision_ts)
        self.assertIsNotNone(rec.decision_ts.tzinfo)

    def test_invalid_action_rejected(self):
        with self.assertRaises(ValueError):
            DecisionRecord(market=Market.KR, ticker="005930", action="MAYBE")

    def test_score_bounds_enforced(self):
        with self.assertRaises(ValueError):
            DecisionRecord(
                market=Market.KR, ticker="005930", action="HOLD",
                fundamental=ModuleScore(score=200.0),
            )

    def test_confidence_bounds_enforced(self):
        with self.assertRaises(ValueError):
            DecisionRecord(
                market=Market.KR, ticker="005930", action="HOLD",
                technical=ModuleScore(score=0.0, confidence=1.5),
            )

    def test_negative_score_allowed(self):
        rec = DecisionRecord(
            market=Market.KR, ticker="005930", action="SELL",
            information=ModuleScore(score=-90.0, confidence=0.7),
        )
        self.assertEqual(rec.information.score, -90.0)


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed (run `uv sync`)")
class RecordRiskSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        # When flush is called, populate id on the added row.
        def _flush():
            added = self.session.add.call_args[0][0]
            added.id = 42
        self.session.flush.side_effect = _flush

    def test_writes_passing_snapshot(self):
        result = make_risk_result(passing=True)
        row = record_risk_snapshot(self.session, result)
        self.session.add.assert_called_once()
        self.session.flush.assert_called_once()
        self.assertEqual(row.id, 42)
        self.assertTrue(row.all_passed)
        self.assertIsNone(row.failures)

    def test_writes_failing_snapshot_with_failures(self):
        result = make_risk_result(passing=False)
        row = record_risk_snapshot(self.session, result)
        self.assertFalse(row.all_passed)
        self.assertIsNotNone(row.failures)
        self.assertGreaterEqual(len(row.failures), 1)
        self.assertEqual(row.failures[0]["limit"], "max_lot")


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed (run `uv sync`)")
class RecordDecisionTest(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.session.flush.return_value = None

    def test_basic_record(self):
        rec = DecisionRecord(
            market=Market.US, ticker="AAPL", action="BUY",
            composite_score=42.0,
            fundamental=ModuleScore(score=20.0, confidence=0.8),
            technical=ModuleScore(score=15.0, confidence=0.6),
            information=ModuleScore(score=7.0, confidence=0.5),
            weights={"f": 0.5, "t": 0.3, "i": 0.2},
        )
        row = record_decision(self.session, rec)
        self.session.add.assert_called_once()
        self.assertEqual(row.market, "US")
        self.assertEqual(row.ticker, "AAPL")
        self.assertEqual(row.action, "BUY")
        self.assertEqual(float(row.composite_score), 42.0)
        self.assertEqual(row.weights, {"f": 0.5, "t": 0.3, "i": 0.2})

    def test_empty_dicts_become_null(self):
        rec = DecisionRecord(market=Market.KR, ticker="005930", action="HOLD")
        row = record_decision(self.session, rec)
        # We persist None rather than {} so the JSONB column is clean
        self.assertIsNone(row.weights)
        self.assertIsNone(row.gate_results)


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class RecordDecisionWithRiskTest(unittest.TestCase):
    def test_links_decision_to_risk_snapshot(self):
        session = MagicMock()
        risk_id_counter = {"n": 0}

        def _flush():
            row = session.add.call_args[0][0]
            # First call -> RiskSnapshot, give it id; later DecisionAudit ignores
            if hasattr(row, "all_passed"):
                risk_id_counter["n"] += 1
                row.id = risk_id_counter["n"]
        session.flush.side_effect = _flush

        risk_result = make_risk_result(passing=True)
        rec = DecisionRecord(market=Market.KR, ticker="005930", action="BUY")
        decision_row = record_decision_with_risk(session, rec, risk_result)
        self.assertEqual(rec.risk_snapshot_id, 1)
        self.assertEqual(decision_row.risk_snapshot_id, 1)
        self.assertIn("all_passed", rec.gate_results)

    def test_failing_risk_still_persists_decision(self):
        """Risk failure shouldn't suppress the audit record — it
        should appear with action='REJECTED' (caller's responsibility
        to set that). This test verifies we don't silently swallow."""
        session = MagicMock()
        def _flush():
            row = session.add.call_args[0][0]
            if hasattr(row, "all_passed"):
                row.id = 99
        session.flush.side_effect = _flush

        risk_result = make_risk_result(passing=False)
        rec = DecisionRecord(market=Market.KR, ticker="005930", action="REJECTED")
        decision_row = record_decision_with_risk(session, rec, risk_result)
        self.assertEqual(decision_row.action, "REJECTED")
        self.assertEqual(decision_row.risk_snapshot_id, 99)
        self.assertFalse(rec.gate_results["all_passed"])
        self.assertIn("max_lot", rec.gate_results["failed"])


if __name__ == "__main__":
    unittest.main()
