"""Pure regime-classifier tests — no DB.

Verifies each voting member fires at the documented threshold, the
2-vote-margin guard collapses thin majorities to NEUTRAL, and missing
inputs degrade gracefully to abstention."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from regime.classifier import (  # noqa: E402
    RegimeLabel, classify_regime, trend_vs_sma,
)


# ──────────────────────────────────────────────────────────────────────


class TrendVsSmaTest(unittest.TestCase):
    def test_returns_none_below_window(self):
        self.assertIsNone(trend_vs_sma([1.0, 2.0], window=200))

    def test_above_sma_returns_plus_one(self):
        # 200 zeros + one above
        series = [10.0] * 200 + [12.0]
        self.assertEqual(trend_vs_sma(series, window=200), 1)

    def test_below_sma_returns_minus_one(self):
        series = [10.0] * 200 + [8.0]
        self.assertEqual(trend_vs_sma(series, window=200), -1)

    def test_zero_sma_returns_none(self):
        # Pathological all-zero history
        series = [0.0] * 200 + [1.0]
        self.assertIsNone(trend_vs_sma(series, window=200))


# ──────────────────────────────────────────────────────────────────────


class ClassifyRegimeTest(unittest.TestCase):
    def test_no_inputs_is_neutral(self):
        r = classify_regime(
            vix_level=None, vix_5d_delta=None,
            index_vs_sma200=None,
        )
        self.assertEqual(r.label, RegimeLabel.NEUTRAL)
        self.assertEqual(r.confidence, 0.0)

    def test_strong_risk_on(self):
        # Three RISK_ON votes (vix low, trend down, index up)
        r = classify_regime(
            vix_level=14.0,
            vix_5d_delta=-3.0,
            index_vs_sma200=1,
        )
        self.assertEqual(r.label, RegimeLabel.RISK_ON)
        self.assertGreater(r.confidence, 0.0)

    def test_strong_risk_off(self):
        r = classify_regime(
            vix_level=32.0,
            vix_5d_delta=+4.0,
            index_vs_sma200=-1,
        )
        self.assertEqual(r.label, RegimeLabel.RISK_OFF)

    def test_thin_majority_stays_neutral(self):
        # 2 directional votes, 1-vote margin → still NEUTRAL
        # (vix high → -1, but vix delta flat → 0, index above SMA → +1)
        # Directional votes: -1, +1; margin = 0 → NEUTRAL
        r = classify_regime(
            vix_level=32.0,
            vix_5d_delta=0.0,        # neutral
            index_vs_sma200=1,
        )
        self.assertEqual(r.label, RegimeLabel.NEUTRAL)

    def test_one_directional_vote_is_neutral(self):
        # Only the index vote is directional; others abstain
        r = classify_regime(
            vix_level=22.0,           # in band → 0
            vix_5d_delta=0.5,         # flat → 0
            index_vs_sma200=1,
        )
        self.assertEqual(r.label, RegimeLabel.NEUTRAL)

    def test_yield_curve_inversion_adds_risk_off_vote(self):
        # Inverted curve = -1 vote; combined with high VIX = -1 → 2 votes RISK_OFF
        r = classify_regime(
            vix_level=30.0,
            vix_5d_delta=None,
            index_vs_sma200=None,
            yield_curve_spread=-0.5,
        )
        self.assertEqual(r.label, RegimeLabel.RISK_OFF)

    def test_dxy_weakening_supports_risk_on(self):
        r = classify_regime(
            vix_level=16.0,           # +1
            vix_5d_delta=None,
            index_vs_sma200=None,
            dxy_20d_change_pct=-2.0,  # +1 (weaker dollar)
        )
        self.assertEqual(r.label, RegimeLabel.RISK_ON)

    def test_votes_carry_detail_string(self):
        r = classify_regime(
            vix_level=14.5,
            vix_5d_delta=-3.0,
            index_vs_sma200=1,
        )
        details = " ".join(v.detail for v in r.votes)
        self.assertIn("VIX 14.5", details)
        self.assertIn("200d SMA", details)

    def test_confidence_is_margin_over_total(self):
        # 3 risk_on votes, 0 risk_off → 3/3 = 1.0
        r = classify_regime(
            vix_level=12.0,
            vix_5d_delta=-3.0,
            index_vs_sma200=1,
        )
        self.assertAlmostEqual(r.confidence, 1.0)

    def test_mixed_signals_reduce_confidence(self):
        # 3 RISK_ON (vix level + delta + index) and 2 RISK_OFF
        # (yield_curve inverted + DXY rising) → margin 1, total 5
        # Need at least 2-vote margin to leave NEUTRAL.
        r = classify_regime(
            vix_level=14.0,
            vix_5d_delta=-3.0,
            index_vs_sma200=1,
            yield_curve_spread=-0.5,
            dxy_20d_change_pct=+2.5,
        )
        # margin (3-2)=1 < 2 → NEUTRAL despite directional 3>2
        self.assertEqual(r.label, RegimeLabel.NEUTRAL)


if __name__ == "__main__":
    unittest.main()
