"""Fundamental ratios / sector percentile / scorer tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fundamental.ratios import compute_ratios  # noqa: E402
from fundamental.score import DEFAULT_WEIGHTS, score_fundamental  # noqa: E402
from fundamental.sector_percentile import (  # noqa: E402
    Percentiles,
    percentile_within_sector,
)


class RatiosTest(unittest.TestCase):
    def test_full_ratios_clean_inputs(self):
        current = {
            "REVENUE": 100.0, "OPERATING_INCOME": 20.0, "NET_INCOME": 15.0,
            "TOTAL_ASSETS": 200.0, "TOTAL_EQUITY": 80.0, "TOTAL_LIABILITIES": 120.0,
            "CURRENT_ASSETS": 60.0, "CURRENT_LIABILITIES": 30.0,
            "CFO": 25.0, "CAPEX": -10.0, "EPS_BASIC": 5.0,
            "SHARES_OUTSTANDING": 100.0,
        }
        prior = {"REVENUE": 80.0, "NET_INCOME": 12.0}
        r = compute_ratios(current=current, prior_year=prior, price=50.0)
        self.assertAlmostEqual(r.per, 50.0 / 5.0)
        # market_cap = 50*100 = 5000; PBR = 5000 / 80 = 62.5
        self.assertAlmostEqual(r.pbr, 5000 / 80)
        self.assertAlmostEqual(r.roe, 15 / 80)
        self.assertAlmostEqual(r.roa, 15 / 200)
        self.assertAlmostEqual(r.op_margin, 20 / 100)
        self.assertAlmostEqual(r.net_margin, 15 / 100)
        self.assertAlmostEqual(r.debt_to_equity, 120 / 80)
        self.assertAlmostEqual(r.current_ratio, 60 / 30)
        # FCF = 25 - |−10| = 15; yield = 15 / 5000
        self.assertAlmostEqual(r.fcf_yield, 15 / 5000)
        self.assertAlmostEqual(r.revenue_growth_yoy, (100 - 80) / 80)
        self.assertAlmostEqual(r.earnings_growth_yoy, (15 - 12) / 12)

    def test_missing_concepts_yield_none(self):
        r = compute_ratios(current={}, price=10.0, shares_outstanding=100)
        for name in r.__dataclass_fields__:
            self.assertIsNone(getattr(r, name))

    def test_growth_handles_negative_prior(self):
        # If prior NI was -10 and now +5, growth = (5 - (-10)) / |-10| = +1.5
        r = compute_ratios(
            current={"REVENUE": 0, "NET_INCOME": 5.0},
            prior_year={"REVENUE": 0, "NET_INCOME": -10.0},
        )
        self.assertAlmostEqual(r.earnings_growth_yoy, 1.5)

    def test_zero_denominator_returns_none(self):
        r = compute_ratios(current={"NET_INCOME": 5.0, "TOTAL_EQUITY": 0})
        self.assertIsNone(r.roe)


class SectorPercentileTest(unittest.TestCase):
    def test_lower_better_inverts_rank(self):
        # Sector of 5 tickers; PER values [10, 20, 30, 40, 50]
        # Best PER = 10 → percentile 1.0
        out = percentile_within_sector({"per": [10.0, 20.0, 30.0, 40.0, 50.0]})
        self.assertAlmostEqual(out[0].get("per"), 1.0)
        self.assertAlmostEqual(out[-1].get("per"), 0.0)
        self.assertAlmostEqual(out[2].get("per"), 0.5)

    def test_higher_better_rank(self):
        out = percentile_within_sector({"roe": [0.05, 0.10, 0.15, 0.20, 0.25]})
        self.assertAlmostEqual(out[0].get("roe"), 0.0)
        self.assertAlmostEqual(out[-1].get("roe"), 1.0)

    def test_missing_values_dont_break_others(self):
        out = percentile_within_sector({"roe": [None, 0.1, 0.2, 0.3, 0.4, 0.5]})
        # First ticker has no ROE → None; others ranked among 5 defined values
        self.assertIsNone(out[0].get("roe"))
        self.assertAlmostEqual(out[-1].get("roe"), 1.0)

    def test_min_sector_size_guard(self):
        # Only 3 tickers reporting → below default min 5 → all None
        out = percentile_within_sector({"roe": [0.1, 0.2, 0.3]})
        self.assertTrue(all(p.get("roe") is None for p in out))

    def test_tied_values_share_percentile(self):
        out = percentile_within_sector({"roe": [0.1, 0.1, 0.2, 0.3, 0.4]})
        self.assertAlmostEqual(out[0].get("roe"), out[1].get("roe"))


class ScorerTest(unittest.TestCase):
    def test_neutral_inputs_give_zero(self):
        # Every ratio at 0.5 percentile → score should be 0
        pct = Percentiles(values={name: 0.5 for name in DEFAULT_WEIGHTS})
        fs = score_fundamental(pct)
        self.assertAlmostEqual(fs.score, 0.0, places=2)
        self.assertAlmostEqual(fs.confidence, 1.0)

    def test_top_percentile_pushes_to_positive(self):
        pct = Percentiles(values={name: 1.0 for name in DEFAULT_WEIGHTS})
        fs = score_fundamental(pct)
        self.assertAlmostEqual(fs.score, 100.0)
        self.assertEqual(fs.confidence, 1.0)

    def test_bottom_percentile_pushes_to_negative(self):
        pct = Percentiles(values={name: 0.0 for name in DEFAULT_WEIGHTS})
        fs = score_fundamental(pct)
        self.assertAlmostEqual(fs.score, -100.0)

    def test_missing_ratios_reduce_confidence(self):
        # Half of the weighted ratios present
        present = list(DEFAULT_WEIGHTS.keys())[:5]
        pct = Percentiles(values={name: (1.0 if name in present else None) for name in DEFAULT_WEIGHTS})
        fs = score_fundamental(pct)
        self.assertLess(fs.confidence, 1.0)
        self.assertGreater(fs.confidence, 0.0)

    def test_no_data_yields_zero(self):
        pct = Percentiles(values={name: None for name in DEFAULT_WEIGHTS})
        fs = score_fundamental(pct)
        self.assertEqual(fs.score, 0.0)
        self.assertEqual(fs.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
