"""Decision engine tests — composite + gates + sizer + drift.

Pure-Python (no DB). Runner integration with DB+broker is deferred
to tests/integration/test_decision_runner.py (needs PostgreSQL up)."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from decision.composite import ModuleVerdict, score_composite  # noqa: E402
from decision.drift import (  # noqa: E402
    check_action_mix_drift, check_score_drift,
)
from decision.gates import run_gates  # noqa: E402
from decision.sizer import SizerInputs, size_position  # noqa: E402
from decision.types import Action, DecisionConfig  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Composite
# ──────────────────────────────────────────────────────────────────────


class CompositeTest(unittest.TestCase):
    def test_all_three_modules_present(self):
        c = score_composite(
            fundamental=ModuleVerdict(score=50, confidence=0.8),
            technical=ModuleVerdict(score=30, confidence=0.7),
            information=ModuleVerdict(score=10, confidence=0.6),
        )
        # weights default 0.35/0.40/0.25 → 50*.35 + 30*.40 + 10*.25 = 32
        self.assertAlmostEqual(c.score, 32.0, places=1)
        self.assertEqual(c.contributing_modules, ("F", "T", "I"))
        # weighted conf ≈ (0.8*0.35 + 0.7*0.40 + 0.6*0.25) = 0.71
        self.assertAlmostEqual(c.confidence, 0.71, places=2)

    def test_missing_module_re_scales_weights(self):
        # No I present — score should NOT halve toward 0
        c = score_composite(
            fundamental=ModuleVerdict(score=50, confidence=1.0),
            technical=ModuleVerdict(score=50, confidence=1.0),
            information=ModuleVerdict(),
        )
        self.assertGreater(c.score, 45.0)   # still close to 50
        self.assertLess(c.confidence, 1.0)  # but coverage < 1

    def test_no_modules_zero(self):
        c = score_composite(
            fundamental=ModuleVerdict(), technical=ModuleVerdict(), information=ModuleVerdict(),
        )
        self.assertEqual(c.score, 0.0)
        self.assertEqual(c.confidence, 0.0)
        self.assertEqual(c.contributing_modules, ())

    def test_clamped_to_range(self):
        c = score_composite(
            fundamental=ModuleVerdict(score=-150, confidence=1.0),
            technical=ModuleVerdict(score=-200, confidence=1.0),
            information=ModuleVerdict(score=-100, confidence=1.0),
        )
        self.assertGreaterEqual(c.score, -100.0)

    def test_custom_weights_via_config(self):
        cfg = DecisionConfig(
            weight_fundamental=1.0, weight_technical=0.0, weight_information=0.0,
        )
        c = score_composite(
            fundamental=ModuleVerdict(score=80, confidence=0.9),
            technical=ModuleVerdict(score=-90, confidence=1.0),
            information=ModuleVerdict(score=-90, confidence=1.0),
            config=cfg,
        )
        # Only F counts → score = 80
        self.assertAlmostEqual(c.score, 80.0)


# ──────────────────────────────────────────────────────────────────────
# Gates
# ──────────────────────────────────────────────────────────────────────


class GatesTest(unittest.TestCase):
    def setUp(self):
        self.config = DecisionConfig()

    def _full_composite(self):
        return score_composite(
            fundamental=ModuleVerdict(score=30, confidence=0.6),
            technical=ModuleVerdict(score=30, confidence=0.6),
            information=ModuleVerdict(score=30, confidence=0.6),
        )

    def test_clean_decision_passes(self):
        g = run_gates(composite=self._full_composite(), config=self.config)
        self.assertTrue(g.all_passed, msg=g.notes)
        self.assertEqual(g.failed_gates, ())

    def test_no_modules_fails(self):
        empty = score_composite(
            fundamental=ModuleVerdict(), technical=ModuleVerdict(),
            information=ModuleVerdict(),
        )
        g = run_gates(composite=empty, config=self.config)
        self.assertFalse(g.all_passed)
        self.assertIn("no_module_scores", g.failed_gates)

    def test_weak_module_confidence_blocks(self):
        c = score_composite(
            fundamental=ModuleVerdict(score=30, confidence=0.1),   # below floor 0.3
            technical=ModuleVerdict(score=30, confidence=0.6),
            information=ModuleVerdict(score=30, confidence=0.6),
        )
        g = run_gates(composite=c, config=self.config)
        self.assertFalse(g.all_passed)
        self.assertIn("module_confidence_below_floor", g.failed_gates)

    def test_overall_confidence_below_floor(self):
        c = score_composite(
            fundamental=ModuleVerdict(score=30, confidence=0.35),
            technical=ModuleVerdict(score=30, confidence=0.35),
            information=ModuleVerdict(score=30, confidence=0.35),
        )
        g = run_gates(composite=c, config=self.config)
        self.assertFalse(g.all_passed)
        self.assertIn("composite_confidence_below_floor", g.failed_gates)

    def test_churn_window_blocks(self):
        now = datetime.now(UTC)
        recent = now - timedelta(minutes=10)
        g = run_gates(
            composite=self._full_composite(), config=self.config,
            last_decision_ts=recent, now=now,
        )
        self.assertFalse(g.all_passed)
        self.assertIn("churn_window", g.failed_gates)

    def test_stale_score_blocks(self):
        g = run_gates(
            composite=self._full_composite(), config=self.config,
            score_max_age_hours=72.0,  # > default 48h
        )
        self.assertFalse(g.all_passed)
        self.assertIn("score_stale", g.failed_gates)


# ──────────────────────────────────────────────────────────────────────
# Sizer
# ──────────────────────────────────────────────────────────────────────


class SizerTest(unittest.TestCase):
    def _inputs(self, **overrides):
        defaults = dict(
            account_equity=1_000_000.0, account_currency="KRW",
            current_price=70_000.0, ticker_currency="KRW",
            composite_score=80.0, composite_confidence=0.9,
            recent_atr=700.0,  # 1% of price per day
            fx_to_account=1.0,
        )
        defaults.update(overrides)
        return SizerInputs(**defaults)

    def test_basic_sizing(self):
        out = size_position(self._inputs())
        # base = 0.05 * 0.8 * 0.9 = 0.036
        # vol scale = target 200bps / observed 100bps = 2.0
        # vol_adjusted = 0.072
        # capped at 0.20 → 0.072 stands
        # notional = 0.072 * 1M = 72,000 KRW → 1 share at 70,000
        self.assertGreater(out.shares, 0)
        self.assertGreater(out.notional_account_ccy, 0)

    def test_zero_composite_yields_zero(self):
        out = size_position(self._inputs(composite_score=0.0))
        self.assertEqual(out.shares, 0)

    def test_zero_confidence_yields_zero(self):
        out = size_position(self._inputs(composite_confidence=0.0))
        self.assertEqual(out.shares, 0)

    def test_hard_cap_applied(self):
        out = size_position(self._inputs(
            composite_score=100.0, composite_confidence=1.0,
            recent_atr=10.0,  # tiny ATR → big vol scale
        ))
        self.assertLessEqual(out.capped_fraction, DecisionConfig().max_position_fraction + 1e-9)

    def test_zero_price_aborts(self):
        out = size_position(self._inputs(current_price=0.0))
        self.assertEqual(out.shares, 0)
        self.assertIn("non_positive_inputs", out.breakdown["abstain_reason"])

    def test_rounds_down_below_one_share(self):
        # Small equity, expensive ticker → fractional shares → 0
        out = size_position(self._inputs(
            account_equity=1000.0, current_price=70_000.0,
        ))
        self.assertEqual(out.shares, 0)

    def test_vol_scale_capped_at_3x(self):
        # ATR very tiny → scale would be huge
        out = size_position(self._inputs(
            recent_atr=1.0,           # 1.4bps per day
            composite_score=50.0, composite_confidence=1.0,
        ))
        # scale should not exceed 3.0
        scale = out.breakdown.get("vol_scale")
        if scale is not None:
            self.assertLessEqual(scale, 3.0 + 1e-9)


# ──────────────────────────────────────────────────────────────────────
# Drift
# ──────────────────────────────────────────────────────────────────────


class ScoreDriftTest(unittest.TestCase):
    def test_short_history_never_flags(self):
        d = check_score_drift(current_score=999, history=[1, 2, 3], min_history=10)
        self.assertFalse(d.is_anomaly)
        self.assertEqual(d.n_history, 3)

    def test_normal_score_not_anomaly(self):
        history = list(range(20))  # 0..19
        d = check_score_drift(current_score=10, history=history)
        self.assertFalse(d.is_anomaly)

    def test_extreme_score_is_anomaly(self):
        history = [10.0] * 20      # zero variance + current differs
        d = check_score_drift(current_score=80, history=history)
        self.assertTrue(d.is_anomaly)

    def test_z_threshold_at_3(self):
        history = list(range(20))  # mean 9.5, stdev ≈ 5.77
        # value at +3σ
        target = 9.5 + 3 * 5.77
        d = check_score_drift(current_score=target, history=history)
        self.assertTrue(d.is_anomaly)


class ActionMixDriftTest(unittest.TestCase):
    def test_too_few_today_skips(self):
        d = check_action_mix_drift(today_actions=["BUY"], history_actions=["BUY"] * 100)
        self.assertFalse(d.is_anomaly)

    def test_identical_mixes_zero_distance(self):
        same = ["BUY"] * 30 + ["HOLD"] * 30 + ["SELL"] * 30
        d = check_action_mix_drift(today_actions=same, history_actions=same)
        self.assertAlmostEqual(d.total_variation_distance, 0.0)
        self.assertFalse(d.is_anomaly)

    def test_complete_shift_flags(self):
        today = ["BUY"] * 50
        history = ["SELL"] * 100
        d = check_action_mix_drift(today_actions=today, history_actions=history)
        self.assertGreater(d.total_variation_distance, 0.5)
        self.assertTrue(d.is_anomaly)


if __name__ == "__main__":
    unittest.main()
