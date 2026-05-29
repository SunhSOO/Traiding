"""Drift route tests with mocked AsyncSession.

Pins response shape + filter handling. Statistical correctness of
the z-score / TV distance computation lives in
``test_decision_drift.py``; these tests only verify that the route
composes the right ``check_*_drift`` call and packages results into
the response model."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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

    from routes.drift import action_mix, score_drift  # noqa: E402


def _make_session(rows):
    """AsyncSession.execute(...) returns an object with .all() → rows."""
    session = MagicMock()

    async def _execute(stmt):
        r = MagicMock()
        r.all = MagicMock(return_value=rows)
        return r

    session.execute = AsyncMock(side_effect=_execute)
    return session


# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps")
class ScoreDriftTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_market_returns_empty(self):
        session = _make_session([])
        out = await score_drift(
            user=None, db=session, market="ZZ",
            z_threshold=3.0, min_history=5, history_days=30,
        )
        self.assertEqual(out, [])

    async def test_too_few_decisions_skipped(self):
        # Only 3 rows for the ticker → less than min_history(5) + 1 baseline
        now = datetime.now(UTC)
        rows = [
            ("KR", "005930", 0.5, now),
            ("KR", "005930", 0.4, now - timedelta(days=1)),
            ("KR", "005930", 0.3, now - timedelta(days=2)),
        ]
        session = _make_session(rows)
        out = await score_drift(
            user=None, db=session, market=None,
            z_threshold=3.0, min_history=5, history_days=30,
        )
        self.assertEqual(out, [])

    async def test_anomaly_surfaces_with_z_above_threshold(self):
        # 10 baseline scores around 0.1 — then current jumps to 0.95
        now = datetime.now(UTC)
        baseline = [
            ("KR", "005930", 0.10 + 0.01 * i, now - timedelta(days=10 - i))
            for i in range(10)
        ]
        # Most-recent row first per route's ORDER BY DESC(decision_ts)
        rows = [("KR", "005930", 0.95, now)] + list(reversed(baseline))
        session = _make_session(rows)
        out = await score_drift(
            user=None, db=session, market="KR",
            z_threshold=3.0, min_history=5, history_days=30,
        )
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row.market, "KR")
        self.assertEqual(row.ticker, "005930")
        self.assertAlmostEqual(row.current_score, 0.95)
        self.assertTrue(row.is_anomaly)
        self.assertGreater(abs(row.z_score), 3.0)

    async def test_anomalies_sorted_first_then_by_abs_z(self):
        now = datetime.now(UTC)
        # Ticker A: anomalous (|z| large)
        a_rows = [("KR", "AAA", 0.95, now)] + [
            ("KR", "AAA", 0.10, now - timedelta(days=i)) for i in range(1, 11)
        ]
        # Ticker B: not anomalous, but with biggest |z| among non-anomalies
        b_rows = [("KR", "BBB", 0.20, now)] + [
            ("KR", "BBB", 0.10 + 0.001 * i, now - timedelta(days=i))
            for i in range(1, 11)
        ]
        rows = a_rows + b_rows
        session = _make_session(rows)
        out = await score_drift(
            user=None, db=session, market=None,
            z_threshold=3.0, min_history=5, history_days=30,
        )
        # AAA (anomaly) ranks before BBB (non-anomaly), regardless of |z|
        self.assertEqual([r.ticker for r in out], ["AAA", "BBB"])


@unittest.skipUnless(_HAVE_DEPS, "deps")
class ActionMixTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_data_returns_zero_distance(self):
        session = _make_session([])
        out = await action_mix(
            user=None, db=session, market=None,
            today_window_hours=24, history_window_days=30, tv_threshold=0.25,
        )
        self.assertEqual(out.today_count, 0)
        self.assertEqual(out.history_count, 0)
        self.assertEqual(out.total_variation_distance, 0.0)
        self.assertFalse(out.is_anomaly)

    async def test_distributions_computed(self):
        now = datetime.now(UTC)
        # Two execute() calls inside the route: today, then history.
        # Use side_effect on the side of execute via two different .all()s.
        session = MagicMock()
        # Today: 4 BUYs, 1 SELL  → 80/20
        today_rows = [("BUY", now)] * 4 + [("SELL", now)]
        # History: 5 BUY, 5 SELL → 50/50
        hist_rows = [("BUY", now - timedelta(days=2))] * 5 \
                  + [("SELL", now - timedelta(days=3))] * 5

        calls = [today_rows, hist_rows]

        async def _execute(stmt):
            r = MagicMock()
            r.all = MagicMock(return_value=calls.pop(0))
            return r

        session.execute = AsyncMock(side_effect=_execute)
        out = await action_mix(
            user=None, db=session, market="KR",
            today_window_hours=24, history_window_days=30, tv_threshold=0.25,
        )
        self.assertEqual(out.today_count, 5)
        self.assertEqual(out.history_count, 10)
        self.assertAlmostEqual(out.today_distribution["BUY"], 0.8)
        self.assertAlmostEqual(out.history_distribution["BUY"], 0.5)
        # TV = ½(|0.8-0.5| + |0.2-0.5|) = 0.3
        self.assertAlmostEqual(out.total_variation_distance, 0.3, places=3)
        self.assertTrue(out.is_anomaly)  # 0.3 ≥ 0.25 threshold

    async def test_invalid_market_falls_through_without_filter(self):
        # Route only logs/ignores bad markets — make sure no crash.
        session = _make_session([])
        out = await action_mix(
            user=None, db=session, market="invalid-zz",
            today_window_hours=24, history_window_days=30, tv_threshold=0.25,
        )
        # Empty data, but route should still return cleanly.
        self.assertEqual(out.today_count, 0)


if __name__ == "__main__":
    unittest.main()
