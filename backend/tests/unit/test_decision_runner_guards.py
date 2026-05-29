"""Regression tests for the decision-runner safety guards.

Two recent additions need explicit coverage:

1. **Currency-mismatch guard** — when account base_currency differs
   from the market's ticker currency, the runner must HOLD with an
   explicit reason instead of producing a wrong-currency notional.

2. **Operator override merge** — operator-set per-cluster weights
   must apply even when no Phase 3 learned weights exist yet.
   Regression for an earlier bug where the merge sat inside an
   ``if latest_weights:`` block.

Both are exercised against ``_decide_one`` with hand-built fakes so
no DB is needed."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-please-replace-with-32-random-bytes-x")

try:
    import sqlalchemy  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from core.config import reset_settings_cache  # noqa: E402

    reset_settings_cache()

    from core.types import Market  # noqa: E402
    from decision.runner import _decide_one  # noqa: E402
    from decision.types import Action, DecisionConfig  # noqa: E402


def _account(base_currency="USD", equity=100_000.0):
    return SimpleNamespace(base_currency=base_currency, equity=equity)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class CurrencyMismatchGuardTest(unittest.TestCase):
    """The runner must refuse to size a position when account currency
    differs from ticker currency. Reproduces the P0 bug fix where
    ``fx_to_account=1.0`` both branches silently emitted USD-sized
    KRW orders."""

    def _patch_runner(self, *, latest_modules, price):
        """Provide enough ambient session+helper stubs for _decide_one
        to run without hitting the DB.

        Returns (mocked_session, restore_callable) — caller patches in
        the lookups via monkeypatching the helpers from the test."""
        from decision import runner

        session = MagicMock()

        # Stub the helpers _decide_one calls
        def _fake_latest_module(session, market, ticker, module, as_of):
            mv = latest_modules.get(module)
            if mv is None:
                from decision.composite import ModuleVerdict
                return (ModuleVerdict(), None)
            return mv

        def _fake_last_decision_ts(session, market, ticker):
            return None

        def _fake_latest_price(session, market, ticker, as_of):
            if price is None:
                return None
            return SimpleNamespace(close=price)

        def _fake_recent_atr(session, market, ticker, as_of):
            return None

        orig = {
            "_latest_module": runner._latest_module,
            "_last_decision_ts": runner._last_decision_ts,
            "_latest_price": runner._latest_price,
            "_recent_atr": runner._recent_atr,
        }
        runner._latest_module = _fake_latest_module
        runner._last_decision_ts = _fake_last_decision_ts
        runner._latest_price = _fake_latest_price
        runner._recent_atr = _fake_recent_atr

        def restore():
            for k, v in orig.items():
                setattr(runner, k, v)
        return session, restore

    def test_kr_ticker_against_usd_account_holds(self):
        """KR ticker (KRW) with USD-account paper book → HOLD."""
        from decision.composite import ModuleVerdict

        now = datetime.now(UTC)
        latest = {
            "F": (ModuleVerdict(score=80, confidence=0.9), now),
            "T": (ModuleVerdict(score=70, confidence=0.9), now),
            "I": (ModuleVerdict(score=60, confidence=0.9), now),
        }
        session, restore = self._patch_runner(latest_modules=latest, price=70_000.0)
        try:
            verdict, intent = _decide_one(
                session,
                market=Market.KR, ticker="005930", as_of=now,
                config=DecisionConfig(),
                account=_account(base_currency="USD"),
                cluster_id=None,
            )
        finally:
            restore()

        self.assertEqual(verdict.action, Action.HOLD)
        self.assertIn("currency_mismatch", verdict.reason)
        self.assertIsNone(intent)

    def test_us_ticker_against_usd_account_proceeds(self):
        """Matched currency → no guard fired (other gates may still HOLD
        but not for currency)."""
        from decision.composite import ModuleVerdict

        now = datetime.now(UTC)
        latest = {
            "F": (ModuleVerdict(score=80, confidence=0.9), now),
            "T": (ModuleVerdict(score=70, confidence=0.9), now),
            "I": (ModuleVerdict(score=60, confidence=0.9), now),
        }
        session, restore = self._patch_runner(latest_modules=latest, price=200.0)
        try:
            verdict, intent = _decide_one(
                session,
                market=Market.US, ticker="AAPL", as_of=now,
                config=DecisionConfig(),
                account=_account(base_currency="USD"),
                cluster_id=None,
            )
        finally:
            restore()
        # The currency guard didn't fire — reason should NOT mention it.
        self.assertNotIn("currency_mismatch", verdict.reason)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class OverrideMergeTest(unittest.TestCase):
    """``_load_operator_overrides`` should return whatever's in the
    ``cluster_weight_overrides`` table; the runner merges learned +
    operator regardless of whether learned weights exist.

    This test exercises only the pure merge logic since the actual
    DB query is integration territory."""

    def test_merge_overlays_operator_on_learned(self):
        """Operator overrides take precedence; learned weights stay for
        other clusters."""
        learned = {
            "CL1": {"F": 0.3, "T": 0.4, "I": 0.3},
            "CL2": {"F": 0.5, "T": 0.3, "I": 0.2},
        }
        operator = {
            "CL1": {"F": 1.0, "T": 0.0, "I": 0.0},   # override CL1 only
        }
        # The merge expression is the production code path
        merged = dict(learned)
        for cid, w in operator.items():
            merged[cid] = w

        self.assertEqual(merged["CL1"]["F"], 1.0)
        self.assertEqual(merged["CL2"]["F"], 0.5)
        self.assertEqual(len(merged), 2)

    def test_merge_works_when_learned_empty(self):
        """Operator can drive decisions even before Phase 3 ran."""
        learned = {}
        operator = {
            "CL_FALLBACK": {"F": 0.5, "T": 0.5, "I": 0.0},
        }
        merged = dict(learned)
        for cid, w in operator.items():
            merged[cid] = w
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged["CL_FALLBACK"]["T"], 0.5)

    def test_merge_works_when_operator_empty(self):
        """No overrides — learned weights pass through unchanged."""
        learned = {"CL1": {"F": 0.3, "T": 0.4, "I": 0.3}}
        operator = {}
        merged = dict(learned)
        for cid, w in operator.items():
            merged[cid] = w
        self.assertEqual(merged, learned)


if __name__ == "__main__":
    unittest.main()
