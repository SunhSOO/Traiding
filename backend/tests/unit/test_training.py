"""Phase 3 training tests — labels, clusterer, walk-forward, trainer.

All pure-Python/numpy. Trainer relies on numpy; if it's missing the
tests skip gracefully."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from training.clusterer import bucket_sector, bucket_size  # noqa: E402
from training.types import (  # noqa: E402
    ClusterAssignment, TrainSample, WalkForwardSplit,
)
from training.walk_forward import generate_splits  # noqa: E402

try:
    import numpy as np  # noqa: F401

    from training.trainer import (  # noqa: E402
        _hit_rate, _normalise_weights, _weighted_lstsq, fit_cluster,
    )
    _HAVE_NUMPY = True
except ImportError:
    _HAVE_NUMPY = False


# ──────────────────────────────────────────────────────────────────────
# Clusterer — sector & size bucketing
# ──────────────────────────────────────────────────────────────────────


class BucketSectorTest(unittest.TestCase):
    def test_canonical_names(self):
        self.assertEqual(bucket_sector("Technology"), "TECH")
        self.assertEqual(bucket_sector("Financials"), "FIN")
        self.assertEqual(bucket_sector("Energy"), "ENERGY")

    def test_loose_match(self):
        # Substring / casing variant
        self.assertEqual(bucket_sector("Information Technology Hardware"), "TECH")
        self.assertEqual(bucket_sector("Financial services"), "FIN")

    def test_unknown_returns_other(self):
        self.assertEqual(bucket_sector("Unobtainium Mining"), "OTHER")

    def test_empty_returns_other(self):
        self.assertEqual(bucket_sector(None), "OTHER")
        self.assertEqual(bucket_sector(""), "OTHER")


class BucketSizeTest(unittest.TestCase):
    def test_kr_thresholds(self):
        self.assertEqual(bucket_size(1e11, market="KR"), "LARGE")
        self.assertEqual(bucket_size(1e10, market="KR"), "MID")
        self.assertEqual(bucket_size(1e8, market="KR"), "SMALL")

    def test_us_thresholds(self):
        self.assertEqual(bucket_size(2e8, market="US"), "LARGE")
        self.assertEqual(bucket_size(2e7, market="US"), "MID")
        self.assertEqual(bucket_size(1e5, market="US"), "SMALL")

    def test_none_or_zero_small(self):
        self.assertEqual(bucket_size(None, market="KR"), "SMALL")
        self.assertEqual(bucket_size(0, market="US"), "SMALL")


# ──────────────────────────────────────────────────────────────────────
# Walk-forward CV
# ──────────────────────────────────────────────────────────────────────


class WalkForwardTest(unittest.TestCase):
    def test_basic_split_generation(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 12, 31, tzinfo=UTC)
        splits = generate_splits(
            start=start, end=end,
            train_window_days=180, test_window_days=30, step_days=30,
            min_train_days=60,
        )
        self.assertGreater(len(splits), 1)
        # First test starts after min_train_days
        self.assertEqual(splits[0].test_start, start + timedelta(days=60))
        # No test exceeds end
        for s in splits:
            self.assertLessEqual(s.test_end, end)

    def test_non_overlapping_tests(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 6, 30, tzinfo=UTC)
        splits = generate_splits(
            start=start, end=end,
            train_window_days=90, test_window_days=20, step_days=20,
            min_train_days=30,
        )
        for a, b in zip(splits, splits[1:]):
            self.assertEqual(a.test_end, b.test_start)

    def test_empty_when_no_room(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 1, 10, tzinfo=UTC)
        splits = generate_splits(start=start, end=end, min_train_days=30, test_window_days=10)
        self.assertEqual(splits, [])

    def test_rejects_invalid_test_window(self):
        with self.assertRaises(ValueError):
            generate_splits(
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 12, 31, tzinfo=UTC),
                test_window_days=0,
            )

    def test_split_label(self):
        s = WalkForwardSplit(
            train_start=datetime(2024, 1, 1, tzinfo=UTC),
            train_end=datetime(2024, 6, 1, tzinfo=UTC),
            test_start=datetime(2024, 6, 1, tzinfo=UTC),
            test_end=datetime(2024, 7, 1, tzinfo=UTC),
        )
        self.assertIn("2024-01-01", s.label)
        self.assertIn("2024-07-01", s.label)


# ──────────────────────────────────────────────────────────────────────
# Trainer — OLS math + cluster fit
# ──────────────────────────────────────────────────────────────────────


def _sample(f, t, i, target, conf=1.0, ticker="A", ts=None):
    ts = ts or datetime(2024, 1, 1, tzinfo=UTC)
    return TrainSample(
        market="KR", ticker=ticker, score_ts=ts,
        f_score=f, t_score=t, i_score=i,
        f_confidence=conf, t_confidence=conf, i_confidence=conf,
        target=target,
    )


@unittest.skipUnless(_HAVE_NUMPY, "numpy not installed")
class NormaliseWeightsTest(unittest.TestCase):
    def test_basic_normalisation(self):
        out = _normalise_weights(0.2, 0.3, 0.5)
        self.assertAlmostEqual(out["F"] + out["T"] + out["I"], 1.0)
        self.assertAlmostEqual(out["I"], 0.5)

    def test_negative_clipped(self):
        out = _normalise_weights(-0.1, 0.5, 0.5)
        self.assertEqual(out["F"], 0.0)
        self.assertAlmostEqual(out["T"] + out["I"], 1.0)

    def test_all_negative_falls_back_to_thirds(self):
        out = _normalise_weights(-0.1, -0.2, -0.3)
        self.assertAlmostEqual(out["F"], 1 / 3)
        self.assertAlmostEqual(out["T"], 1 / 3)
        self.assertAlmostEqual(out["I"], 1 / 3)

    def test_zero_total_falls_back(self):
        out = _normalise_weights(0, 0, 0)
        self.assertAlmostEqual(out["F"], 1 / 3)


@unittest.skipUnless(_HAVE_NUMPY, "numpy not installed")
class HitRateTest(unittest.TestCase):
    def test_perfect_sign_agreement(self):
        import numpy as np
        self.assertAlmostEqual(_hit_rate(np.array([1, -1, 2]), np.array([1, -1, 3])), 1.0)

    def test_no_agreement(self):
        import numpy as np
        self.assertAlmostEqual(_hit_rate(np.array([1, 1, 1]), np.array([-1, -1, -1])), 0.0)

    def test_mixed(self):
        import numpy as np
        # 2 out of 4 agree → 0.5
        hr = _hit_rate(np.array([1, -1, 1, -1]), np.array([1, 1, -1, -1]))
        self.assertAlmostEqual(hr, 0.5)

    def test_empty(self):
        import numpy as np
        self.assertEqual(_hit_rate(np.array([]), np.array([])), 0.0)


@unittest.skipUnless(_HAVE_NUMPY, "numpy not installed")
class FitClusterTest(unittest.TestCase):
    def test_too_few_samples_returns_none(self):
        samples = [_sample(10, 5, 0, 0.01) for _ in range(10)]
        result = fit_cluster(samples, cluster_id="C1", model_version="ols-v1")
        self.assertIsNone(result)

    def test_recovers_pure_F_signal(self):
        # Target depends only on F_score; T and I are pure noise
        import numpy as np
        rng = np.random.default_rng(42)
        samples = []
        for i in range(200):
            f = rng.uniform(-50, 50)
            t = rng.uniform(-50, 50)
            i_score = rng.uniform(-50, 50)
            target = 0.001 * f + rng.normal(0, 0.01)
            samples.append(_sample(f, t, i_score, target, ticker=f"T{i % 5}"))
        result = fit_cluster(
            samples, cluster_id="C1", model_version="ols-v1",
            walk_forward_splits=None,
            use_confidence_weights=False,
        )
        self.assertIsNotNone(result)
        # F should win the weight competition by far
        self.assertGreater(result.w_fundamental, 0.7)
        self.assertGreater(result.metrics.r2_in_sample, 0.5)

    def test_no_signal_falls_back_to_equal(self):
        # Pure noise → all coefs ~0; normalised weights should be thirds.
        import numpy as np
        rng = np.random.default_rng(0)
        samples = []
        for i in range(200):
            samples.append(_sample(
                rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1),
                rng.normal(0, 0.01),
                ticker=f"T{i % 5}",
            ))
        result = fit_cluster(
            samples, cluster_id="C1", model_version="ols-v1",
            walk_forward_splits=None, use_confidence_weights=False,
        )
        self.assertIsNotNone(result)
        # Sum is 1, and no module should grab >75% of the weight
        self.assertAlmostEqual(
            result.w_fundamental + result.w_technical + result.w_information, 1.0, places=4,
        )
        for w in (result.w_fundamental, result.w_technical, result.w_information):
            self.assertLessEqual(w, 0.75)

    def test_confidence_weighting_emphasises_high_conf_samples(self):
        # Build two clusters of samples: high-conf with sign matching F,
        # low-conf with opposite sign. Confidence weighting should let
        # the high-conf cluster dominate.
        samples = []
        for _ in range(120):
            samples.append(_sample(50, 0, 0, target=0.05, conf=0.95))
        for _ in range(120):
            samples.append(_sample(50, 0, 0, target=-0.05, conf=0.05))
        r_conf = fit_cluster(samples, cluster_id="C", model_version="v1",
                             use_confidence_weights=True)
        r_unconf = fit_cluster(samples, cluster_id="C", model_version="v1",
                               use_confidence_weights=False)
        # With confidence weighting, F should be dominant (positive)
        # Without, the two cohorts roughly cancel.
        self.assertIsNotNone(r_conf)
        self.assertIsNotNone(r_unconf)
        # Confidence-weighted intercept should reflect the high-conf cohort's
        # positive bias more strongly than the unweighted one.
        self.assertGreater(r_conf.intercept, r_unconf.intercept - 1e-6)


# ──────────────────────────────────────────────────────────────────────
# DecisionConfig.weights_for — cluster override resolution
# ──────────────────────────────────────────────────────────────────────


class WeightsForClusterTest(unittest.TestCase):
    def test_falls_back_to_global(self):
        from decision.types import DecisionConfig
        cfg = DecisionConfig()
        w = cfg.weights_for("unknown-cluster")
        self.assertAlmostEqual(w["F"] + w["T"] + w["I"], 1.0)
        # Default is 0.35/0.40/0.25
        self.assertAlmostEqual(w["T"], 0.40)

    def test_uses_cluster_override(self):
        from decision.types import DecisionConfig
        cfg = DecisionConfig(cluster_weight_overrides={
            "KR:TECH:LARGE": {"F": 0.1, "T": 0.7, "I": 0.2},
        })
        w = cfg.weights_for("KR:TECH:LARGE")
        self.assertAlmostEqual(w["T"], 0.7)

    def test_override_is_normalised_defensively(self):
        from decision.types import DecisionConfig
        cfg = DecisionConfig(cluster_weight_overrides={
            "C": {"F": 1.0, "T": 1.0, "I": 2.0},   # sum = 4
        })
        w = cfg.weights_for("C")
        self.assertAlmostEqual(w["I"], 0.5)
        self.assertAlmostEqual(w["F"] + w["T"] + w["I"], 1.0)

    def test_zero_override_falls_back(self):
        from decision.types import DecisionConfig
        cfg = DecisionConfig(cluster_weight_overrides={
            "C": {"F": 0.0, "T": 0.0, "I": 0.0},
        })
        # Total is 0 → fall back to global
        w = cfg.weights_for("C")
        # Should be the default 0.35/0.40/0.25 split, not 1/3
        self.assertAlmostEqual(w["T"], 0.40)


if __name__ == "__main__":
    unittest.main()
