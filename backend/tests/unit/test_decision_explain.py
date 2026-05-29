"""Tests for the decision-detail explainability helpers.

Covers ``_contributions`` and ``_derive_reason`` from
``routes/decision.py`` — pure functions over DecisionAudit-shaped
inputs, but importing the module requires fastapi/sqlalchemy so we
gate with the standard deps-skip pattern."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-please-replace-with-32-random-bytes-x")

try:
    import fastapi  # noqa: F401
    import sqlalchemy  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from core.config import reset_settings_cache  # noqa: E402

    reset_settings_cache()

    from routes.decision import _contributions, _derive_reason  # noqa: E402


def _row(**overrides):
    """Build a DecisionAudit-shaped namespace with sensible defaults."""
    defaults = dict(
        action="HOLD",
        fundamental_score=None, technical_score=None, information_score=None,
        composite_score=None, composite_confidence=None,
        weights=None, gate_results=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps")
class ContributionsTest(unittest.TestCase):
    def test_empty_when_no_weights(self):
        row = _row(fundamental_score=80, technical_score=50)
        self.assertEqual(_contributions(row), [])

    def test_skips_missing_score(self):
        row = _row(
            fundamental_score=80, technical_score=None, information_score=10,
            weights={"F": 0.5, "T": 0.3, "I": 0.2},
        )
        out = _contributions(row)
        self.assertEqual([c["module"] for c in out], ["F", "I"])
        self.assertAlmostEqual(out[0]["contribution"], 80 * 0.5)
        self.assertAlmostEqual(out[1]["contribution"], 10 * 0.2)

    def test_all_three_modules(self):
        row = _row(
            fundamental_score=60, technical_score=40, information_score=20,
            weights={"F": 0.4, "T": 0.4, "I": 0.2},
        )
        out = _contributions(row)
        self.assertEqual(len(out), 3)
        total = sum(c["contribution"] for c in out)
        # Composite check: 60*0.4 + 40*0.4 + 20*0.2 = 24 + 16 + 4 = 44
        self.assertAlmostEqual(total, 44.0)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class DeriveReasonTest(unittest.TestCase):
    def test_rejected_lists_failed_risk_limits(self):
        row = _row(action="REJECTED")
        risk = {
            "max_lot_pass": False,
            "daily_loss_pass": True,
            "consecutive_loss_pass": True,
            "max_positions_pass": True,
            "spread_pass": False,
            "symbol_allowed_pass": True,
        }
        reason = _derive_reason(row, risk)
        self.assertIn("최대 lot", reason)
        self.assertIn("스프레드", reason)

    def test_buy_above_threshold(self):
        row = _row(action="BUY", composite_score=42.3)
        reason = _derive_reason(row, None)
        self.assertIn("+42.3", reason)
        self.assertIn("BUY", reason)

    def test_sell_below_threshold(self):
        row = _row(action="SELL", composite_score=-35.1)
        reason = _derive_reason(row, None)
        self.assertIn("-35.1", reason)
        self.assertIn("SELL", reason)

    def test_hold_with_gate_failure(self):
        row = _row(
            action="HOLD",
            composite_score=10.0,
            gate_results={
                "min_confidence": {"passed": False, "reason": "below floor"},
                "staleness": {"passed": True},
            },
        )
        reason = _derive_reason(row, None)
        self.assertIn("min_confidence", reason)

    def test_hold_no_gate_failure_reports_score(self):
        row = _row(action="HOLD", composite_score=10.0, gate_results={})
        reason = _derive_reason(row, None)
        self.assertIn("+10.0", reason)


if __name__ == "__main__":
    unittest.main()
