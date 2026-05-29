"""Regime-aware DecisionConfig math tests.

Pure config-level checks. The runner-side wire-in is covered by
``test_decision_runner_guards.py`` (needs sqlalchemy)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from decision.types import DecisionConfig  # noqa: E402


class RegimeThresholdScalingTest(unittest.TestCase):
    def test_no_regime_returns_raw_thresholds(self):
        cfg = DecisionConfig(buy_threshold=25.0, sell_threshold=-25.0)
        self.assertEqual(cfg.buy_threshold_for_regime(None), 25.0)
        self.assertEqual(cfg.sell_threshold_for_regime(None), -25.0)

    def test_risk_off_tightens_buy_relaxes_sell(self):
        cfg = DecisionConfig(buy_threshold=25.0, sell_threshold=-25.0)
        # Defaults: RISK_OFF buy=1.30, sell=0.80
        self.assertAlmostEqual(cfg.buy_threshold_for_regime("RISK_OFF"), 25.0 * 1.30)
        # sell scaler 0.80 → magnitude shrinks → easier to fire SELL
        self.assertAlmostEqual(cfg.sell_threshold_for_regime("RISK_OFF"), -25.0 * 0.80)

    def test_risk_on_relaxes_buy_tightens_sell(self):
        cfg = DecisionConfig(buy_threshold=25.0, sell_threshold=-25.0)
        # Defaults: RISK_ON buy=0.85, sell=1.15
        self.assertAlmostEqual(cfg.buy_threshold_for_regime("RISK_ON"), 25.0 * 0.85)
        self.assertAlmostEqual(cfg.sell_threshold_for_regime("RISK_ON"), -25.0 * 1.15)

    def test_neutral_is_passthrough(self):
        cfg = DecisionConfig(buy_threshold=25.0, sell_threshold=-25.0)
        self.assertEqual(cfg.buy_threshold_for_regime("NEUTRAL"), 25.0)
        self.assertEqual(cfg.sell_threshold_for_regime("NEUTRAL"), -25.0)

    def test_unknown_regime_falls_back_to_raw(self):
        cfg = DecisionConfig(buy_threshold=25.0, sell_threshold=-25.0)
        self.assertEqual(cfg.buy_threshold_for_regime("BANANA"), 25.0)
        self.assertEqual(cfg.sell_threshold_for_regime("BANANA"), -25.0)


class RegimeSizeScalingTest(unittest.TestCase):
    def test_no_regime_returns_base_fraction(self):
        cfg = DecisionConfig(base_position_fraction=0.05)
        self.assertEqual(cfg.size_fraction_for_regime(None), 0.05)

    def test_risk_off_halves_size(self):
        cfg = DecisionConfig(base_position_fraction=0.05)
        # Defaults: RISK_OFF size scaler 0.50
        self.assertAlmostEqual(cfg.size_fraction_for_regime("RISK_OFF"), 0.025)

    def test_risk_on_keeps_full_size(self):
        cfg = DecisionConfig(base_position_fraction=0.05)
        # Defaults: RISK_ON size scaler 1.00
        self.assertAlmostEqual(cfg.size_fraction_for_regime("RISK_ON"), 0.05)

    def test_neutral_dampens_slightly(self):
        cfg = DecisionConfig(base_position_fraction=0.05)
        # Defaults: NEUTRAL size scaler 0.85
        self.assertAlmostEqual(cfg.size_fraction_for_regime("NEUTRAL"), 0.05 * 0.85)


class CustomRegimeOverrideTest(unittest.TestCase):
    def test_operator_can_disable_regime_with_empty_dicts(self):
        cfg = DecisionConfig(
            buy_threshold=25.0,
            sell_threshold=-25.0,
            base_position_fraction=0.05,
            regime_threshold_scalers={},
            regime_size_scalers={},
        )
        # Empty scaler dict → labels look unknown → raw values
        self.assertEqual(cfg.buy_threshold_for_regime("RISK_OFF"), 25.0)
        self.assertEqual(cfg.sell_threshold_for_regime("RISK_OFF"), -25.0)
        self.assertEqual(cfg.size_fraction_for_regime("RISK_OFF"), 0.05)


if __name__ == "__main__":
    unittest.main()
