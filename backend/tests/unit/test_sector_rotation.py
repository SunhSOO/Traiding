"""Pure sector-rotation math tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.sector_rotation import compute_sector_rotation  # noqa: E402


class SectorRotationTest(unittest.TestCase):
    def test_empty_inputs_returns_empty(self):
        r = compute_sector_rotation(
            recent_returns={}, ticker_to_sector={},
        )
        self.assertEqual(r.sector_scores, {})
        self.assertEqual(r.ticker_bonus, {})

    def test_single_sector_no_z_score(self):
        # Three tickers, all in TECH → not enough sectors for cross-sector z
        r = compute_sector_rotation(
            recent_returns={"A": 0.10, "B": 0.05, "C": 0.08},
            ticker_to_sector={"A": "TECH", "B": "TECH", "C": "TECH"},
        )
        # sector_scores returned but no bonus (insufficient cross-sector data)
        self.assertEqual(r.ticker_bonus, {})
        self.assertIn("insufficient_sectors", r.diagnostics.get("reason", ""))

    def test_leader_sector_gets_positive_bonus(self):
        # ENERGY clearly leads; TECH is mid; UTIL is bottom
        rr = {
            "E1": 0.20, "E2": 0.22, "E3": 0.18,    # ENERGY median 0.20
            "T1": 0.02, "T2": 0.03, "T3": 0.01,    # TECH median 0.02
            "U1": -0.05, "U2": -0.07, "U3": -0.06, # UTIL median -0.06
        }
        sec = {
            "E1": "ENERGY", "E2": "ENERGY", "E3": "ENERGY",
            "T1": "TECH", "T2": "TECH", "T3": "TECH",
            "U1": "UTIL", "U2": "UTIL", "U3": "UTIL",
        }
        r = compute_sector_rotation(recent_returns=rr, ticker_to_sector=sec)
        self.assertGreater(r.sector_scores["ENERGY"], 0.0)
        self.assertLess(r.sector_scores["UTIL"], 0.0)
        # TECH near median → near zero
        self.assertLess(abs(r.sector_scores["TECH"]), abs(r.sector_scores["ENERGY"]))

    def test_bonus_capped_at_configured_limit(self):
        rr = {
            "L1": 1.0, "L2": 1.0, "L3": 1.0,        # extreme leader
            "M1": 0.0, "M2": 0.0, "M3": 0.0,
            "T1": -1.0, "T2": -1.0, "T3": -1.0,     # extreme laggard
        }
        sec = {
            "L1": "LEAD", "L2": "LEAD", "L3": "LEAD",
            "M1": "MID", "M2": "MID", "M3": "MID",
            "T1": "LAG", "T2": "LAG", "T3": "LAG",
        }
        r = compute_sector_rotation(
            recent_returns=rr, ticker_to_sector=sec, bonus_cap=15.0,
        )
        # Z-scores would blow past 1.5σ; we clip to ±1.5 then scale → ±15.0
        self.assertLessEqual(r.sector_scores["LEAD"], 15.0 + 1e-6)
        self.assertGreaterEqual(r.sector_scores["LAG"], -15.0 - 1e-6)

    def test_sector_too_small_dropped(self):
        # SOLO has only 1 ticker; min_tickers_per_sector=3 should exclude it
        rr = {
            "S1": 5.0,    # SOLO sector — dropped
            "A1": 0.0, "A2": 0.1, "A3": 0.2,
            "B1": -0.1, "B2": -0.05, "B3": -0.15,
        }
        sec = {
            "S1": "SOLO",
            "A1": "ALPHA", "A2": "ALPHA", "A3": "ALPHA",
            "B1": "BETA", "B2": "BETA", "B3": "BETA",
        }
        r = compute_sector_rotation(
            recent_returns=rr, ticker_to_sector=sec, min_tickers_per_sector=3,
        )
        self.assertNotIn("SOLO", r.sector_scores)
        self.assertIn("ALPHA", r.sector_scores)
        self.assertIn("BETA", r.sector_scores)

    def test_ticker_bonus_inherits_sector(self):
        rr = {
            "A1": 0.10, "A2": 0.11, "A3": 0.09,
            "B1": -0.05, "B2": -0.06, "B3": -0.04,
        }
        sec = {
            "A1": "ALPHA", "A2": "ALPHA", "A3": "ALPHA",
            "B1": "BETA", "B2": "BETA", "B3": "BETA",
            # A ticker NOT in recent_returns but in sector map — should
            # still get the bonus if its sector was scored.
            "A4": "ALPHA",
        }
        r = compute_sector_rotation(recent_returns=rr, ticker_to_sector=sec)
        self.assertGreater(r.ticker_bonus["A1"], 0.0)
        self.assertAlmostEqual(r.ticker_bonus["A1"], r.ticker_bonus["A4"])
        self.assertLess(r.ticker_bonus["B1"], 0.0)

    def test_missing_sector_for_ticker_drops_it(self):
        rr = {"A": 0.10, "B": 0.05, "C": 0.08}
        sec = {"A": "TECH"}     # B, C unmapped
        r = compute_sector_rotation(recent_returns=rr, ticker_to_sector=sec)
        # Only TECH (1 ticker) → below min_tickers_per_sector → no bonus
        self.assertEqual(r.ticker_bonus, {})


if __name__ == "__main__":
    unittest.main()
