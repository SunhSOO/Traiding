"""Training route tests with mocked AsyncSession.

Verifies summary / list / history shape. DB-side query correctness
is covered by the SQL itself — these tests pin the response composition."""
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

    from core.models.training import ClusterWeights, TickerClusterAssignment  # noqa: E402
    from routes.training import (  # noqa: E402
        cluster_history, list_clusters, training_summary,
    )


def _make_weight(cluster_id, **overrides):
    defaults = dict(
        cluster_id=cluster_id,
        learned_at=datetime.now(UTC),
        w_fundamental=0.4, w_technical=0.4, w_information=0.2,
        intercept=0.001,
        n_samples=500, n_tickers=12,
        model_version="ols-v1",
        metrics={"r2_in_sample": 0.05, "r2_walk_forward": 0.03, "hit_rate": 0.55},
        notes="ok",
    )
    defaults.update(overrides)
    return ClusterWeights(**defaults)


def _make_session_for_summary(latest_ts, latest_rows, ticker_count):
    session = MagicMock()
    # db.scalar(...) used twice: latest_ts + total_tickers_now
    session.scalar = AsyncMock(side_effect=[latest_ts, ticker_count])

    async def _execute(stmt):
        s = str(stmt).lower()
        if "cluster_weights" in s:
            r = MagicMock()
            r.scalars = MagicMock(return_value=iter(latest_rows))
            return r
        if "ticker_clusters" in s:
            r = MagicMock()
            r.all = MagicMock(return_value=[])
            return r
        return MagicMock()

    session.execute = AsyncMock(side_effect=_execute)
    return session


def _make_session_for_list(latest_rows, assignment_rows):
    session = MagicMock()

    async def _execute(stmt):
        s = str(stmt).lower()
        if "ticker_clusters" in s:
            r = MagicMock()
            r.all = MagicMock(return_value=assignment_rows)
            return r
        if "cluster_weights" in s:
            r = MagicMock()
            r.scalars = MagicMock(return_value=iter(latest_rows))
            return r
        return MagicMock()

    session.execute = AsyncMock(side_effect=_execute)
    return session


# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps")
class SummaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_returns_has_data_false(self):
        session = _make_session_for_summary(latest_ts=None, latest_rows=[], ticker_count=0)
        out = await training_summary(user=None, db=session)
        self.assertFalse(out.has_data)
        self.assertEqual(out.clusters_trained, 0)

    async def test_aggregates(self):
        ts = datetime(2026, 5, 28, tzinfo=UTC)
        rows = [
            _make_weight("KR:TECH:LARGE", n_samples=1000),
            _make_weight("US:HEALTH:MID", n_samples=2000, model_version="ols-v2"),
        ]
        session = _make_session_for_summary(latest_ts=ts, latest_rows=rows, ticker_count=350)
        out = await training_summary(user=None, db=session)
        self.assertTrue(out.has_data)
        self.assertEqual(out.clusters_trained, 2)
        self.assertEqual(out.total_samples, 3000)
        self.assertEqual(out.total_tickers_now, 350)
        # Sorted version list
        self.assertEqual(out.model_versions, ["ols-v1", "ols-v2"])


@unittest.skipUnless(_HAVE_DEPS, "deps")
class ListClustersTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty(self):
        session = _make_session_for_list(latest_rows=[], assignment_rows=[])
        out = await list_clusters(user=None, db=session)
        self.assertEqual(out, [])

    async def test_basic_with_ticker_counts(self):
        rows = [
            _make_weight("KR:TECH:LARGE",
                         metrics={"r2_in_sample": 0.10, "hit_rate": 0.60}),
            _make_weight("US:FIN:MID",
                         metrics={"r2_in_sample": 0.02, "hit_rate": 0.48}),
        ]
        # Ticker assignments: 2 to KR:TECH:LARGE, 1 to US:FIN:MID
        ts_now = datetime.now(UTC)
        assigns = [
            ("KR", "005930", "KR:TECH:LARGE", ts_now),
            ("KR", "000660", "KR:TECH:LARGE", ts_now),
            ("US", "JPM", "US:FIN:MID", ts_now),
            # An older row for 005930 in a different cluster — should be ignored
            ("KR", "005930", "KR:TECH:MID", ts_now - timedelta(days=30)),
        ]
        session = _make_session_for_list(latest_rows=rows, assignment_rows=assigns)
        out = await list_clusters(user=None, db=session)
        self.assertEqual(len(out), 2)
        # Sorted by hit_rate desc
        self.assertEqual(out[0].cluster_id, "KR:TECH:LARGE")
        self.assertEqual(out[0].n_tickers_now, 2)
        self.assertAlmostEqual(out[0].hit_rate, 0.60)
        self.assertEqual(out[1].cluster_id, "US:FIN:MID")
        self.assertEqual(out[1].n_tickers_now, 1)

    async def test_missing_metrics_treated_as_none(self):
        rows = [_make_weight("X", metrics=None)]
        session = _make_session_for_list(latest_rows=rows, assignment_rows=[])
        out = await list_clusters(user=None, db=session)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].r2_in_sample)
        self.assertIsNone(out[0].hit_rate)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class ClusterHistoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_history_raises_404(self):
        session = MagicMock()

        async def _execute(stmt):
            r = MagicMock()
            r.scalars = MagicMock(return_value=iter([]))
            return r
        session.execute = AsyncMock(side_effect=_execute)

        with self.assertRaises(Exception) as cm:
            await cluster_history("KR:TECH:LARGE", user=None, db=session)
        self.assertIn("no history", str(cm.exception).lower())

    async def test_returns_rows(self):
        rows = [
            _make_weight("C", learned_at=datetime(2026, 5, 28, tzinfo=UTC)),
            _make_weight("C", learned_at=datetime(2026, 5, 21, tzinfo=UTC)),
        ]
        session = MagicMock()

        async def _execute(stmt):
            r = MagicMock()
            r.scalars = MagicMock(return_value=iter(rows))
            return r
        session.execute = AsyncMock(side_effect=_execute)

        out = await cluster_history("C", user=None, db=session)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].cluster_id, "C")
        self.assertEqual(out[0].n_samples, 500)

    async def test_empty_cluster_id_rejected(self):
        session = MagicMock()
        with self.assertRaises(Exception) as cm:
            await cluster_history("", user=None, db=session)
        self.assertIn("required", str(cm.exception).lower())


if __name__ == "__main__":
    unittest.main()
