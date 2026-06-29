"""Integrated pipeline unit tests — selection/market-read math + timing gates.

Covers the pure decision logic of the selection→execution pipeline:
  * regime → base exposure mapping
  * run_selection breadth / target_exposure / basket weight computation
  * technical-timing threshold sanity (D1: timing only)
Full DB+broker execution is covered by the integration smoke (run_integrated_job).
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import pandas as pd
    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


@unittest.skipUnless(_HAVE_DEPS, "pandas required")
class TestExposureMapping(unittest.TestCase):
    def test_base_exposure_by_regime(self):
        from decision.selection import _base_exposure
        self.assertEqual(_base_exposure("crisis"), 0.0)
        self.assertEqual(_base_exposure("risk_off"), 0.4)
        self.assertEqual(_base_exposure("neutral"), 0.7)
        self.assertEqual(_base_exposure("risk_on"), 1.0)
        self.assertEqual(_base_exposure("calm_bull"), 1.0)
        self.assertEqual(_base_exposure("RISK_ON"), 1.0)        # case-insensitive
        self.assertEqual(_base_exposure(None), 0.7)             # default
        self.assertEqual(_base_exposure("weird_label"), 0.7)    # unknown → default


@unittest.skipUnless(_HAVE_DEPS, "pandas required")
class TestRunSelection(unittest.TestCase):
    def _recs(self):
        # 10 names: 6 with positive pred_ret (breadth 0.6); 3 are BUY (top decile)
        rows = []
        for i in range(10):
            rows.append({
                "ticker": f"T{i}", "rank_pct": 0.95 - i * 0.1,
                "pred_ret": 0.05 if i < 6 else -0.02,
                "target_price": 100 + i, "band_low": 90 + i, "band_high": 110 + i,
                "action": "BUY" if i < 3 else ("SELL" if i >= 9 else "HOLD"),
            })
        return pd.DataFrame(rows)

    def _session(self, regime="neutral", conf=0.6):
        s = MagicMock()
        s.execute.return_value.first.return_value = (regime, conf)
        return s

    def test_breadth_exposure_basket(self):
        from decision.selection import run_selection
        res = run_selection(self._session("neutral"), market="US",
                            as_of=datetime(2026, 6, 29), recs=self._recs(), persist=False)
        self.assertAlmostEqual(res.breadth, 0.6, places=3)        # 6/10 positive
        # neutral base 0.7 × min(1, 0.4+0.6)=1.0 → 0.7
        self.assertAlmostEqual(res.target_exposure, 0.7, places=3)
        self.assertEqual(len(res.basket), 3)                       # 3 BUYs
        self.assertAlmostEqual(res.basket[0].target_weight, 1 / 3, places=4)

    def test_crisis_zero_exposure(self):
        from decision.selection import run_selection
        res = run_selection(self._session("crisis"), market="US",
                            as_of=datetime(2026, 6, 29), recs=self._recs(), persist=False)
        self.assertEqual(res.target_exposure, 0.0)                 # crisis → no exposure

    def test_thin_breadth_reduces_exposure(self):
        from decision.selection import run_selection
        recs = self._recs()
        recs["pred_ret"] = -0.01                                   # breadth 0 (all negative)
        res = run_selection(self._session("neutral"), market="US",
                            as_of=datetime(2026, 6, 29), recs=recs, persist=False)
        # 0.7 × min(1, 0.4+0.0) = 0.28
        self.assertAlmostEqual(res.target_exposure, 0.28, places=3)


@unittest.skipUnless(_HAVE_DEPS, "pandas required")
class TestTimingThresholds(unittest.TestCase):
    def test_timing_constants_sane(self):
        from decision.integrated_runner import ENTRY_MIN, EXIT_MAX, MIN_EXPOSURE
        # exit threshold must be below entry threshold (hysteresis); D1 timing-only
        self.assertLess(EXIT_MAX, ENTRY_MIN)
        self.assertGreater(MIN_EXPOSURE, 0.0)
        self.assertLess(MIN_EXPOSURE, 0.1)


if __name__ == "__main__":
    unittest.main()
