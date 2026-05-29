"""Pure attribution math tests — no SQLAlchemy / FastAPI deps."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.attribution import (  # noqa: E402
    AttributionSample, compute_attribution,
)


def _sample(f=None, t=None, i=None, ret=0.0):
    scores = {}
    if f is not None: scores["F"] = f
    if t is not None: scores["T"] = t
    if i is not None: scores["I"] = i
    return AttributionSample(module_scores=scores, forward_return=ret)


class AttributionBasicTest(unittest.TestCase):
    def test_no_samples_returns_zeros(self):
        out = compute_attribution([])
        self.assertEqual(out.n_samples_total, 0)
        for m in out.modules:
            self.assertEqual(m.n_samples, 0)
            self.assertIsNone(m.pearson_r)
            self.assertEqual(m.attribution_share, 0.0)

    def test_perfect_positive_correlation(self):
        # F predicts return perfectly; T anti-predicts; I zero variance.
        samples = [
            _sample(f=10, t=-10, i=0, ret=0.10),
            _sample(f=20, t=-20, i=0, ret=0.20),
            _sample(f=30, t=-30, i=0, ret=0.30),
            _sample(f=40, t=-40, i=0, ret=0.40),
        ]
        out = compute_attribution(samples)
        by_m = {m.module: m for m in out.modules}
        self.assertAlmostEqual(by_m["F"].pearson_r, 1.0, places=6)
        self.assertAlmostEqual(by_m["T"].pearson_r, -1.0, places=6)
        # I has zero variance → pearson_r = None
        self.assertIsNone(by_m["I"].pearson_r)
        # |r| sums to 2.0 → F share = 0.5, T share = 0.5, I share = 0
        self.assertAlmostEqual(by_m["F"].attribution_share, 0.5)
        self.assertAlmostEqual(by_m["T"].attribution_share, 0.5)
        self.assertEqual(by_m["I"].attribution_share, 0.0)

    def test_sign_accuracy(self):
        # F sign matches return 3/4 of the time, 4th is 0-return excluded
        samples = [
            _sample(f=10, ret=0.05),    # +/+ hit
            _sample(f=-10, ret=-0.05),  # -/- hit
            _sample(f=10, ret=-0.02),   # +/- miss
            _sample(f=0, ret=0.03),     # excluded (score == 0)
        ]
        out = compute_attribution(samples)
        f = next(m for m in out.modules if m.module == "F")
        self.assertAlmostEqual(f.sign_accuracy, 2 / 3)

    def test_module_missing_in_some_samples(self):
        samples = [
            _sample(f=10, t=20, ret=0.10),    # both present
            _sample(f=15, ret=0.15),          # only F
        ]
        out = compute_attribution(samples)
        by_m = {m.module: m for m in out.modules}
        self.assertEqual(by_m["F"].n_samples, 2)
        self.assertEqual(by_m["T"].n_samples, 1)
        # T with 1 sample → r=None
        self.assertIsNone(by_m["T"].pearson_r)

    def test_attribution_share_sums_to_one(self):
        # Three modules with varying |r|.
        samples = [
            _sample(f=10, t=5, i=2, ret=0.10),
            _sample(f=20, t=8, i=1, ret=0.18),
            _sample(f=30, t=3, i=4, ret=0.30),
            _sample(f=40, t=-2, i=2, ret=0.40),
        ]
        out = compute_attribution(samples)
        total_share = sum(m.attribution_share for m in out.modules)
        self.assertAlmostEqual(total_share, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
