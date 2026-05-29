"""Tests for /api/analysis/{m}/{t}/score-history.

- Downsampling preserves endpoints
- Bad-input rejection (days / max_points bounds, unknown market)
- Empty series shape
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
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

    from fastapi import HTTPException  # noqa: E402

    from routes.analysis import _downsample, score_series  # noqa: E402


# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps")
class DownsampleTest(unittest.TestCase):
    def test_short_list_returns_unchanged(self):
        pts = list(range(50))
        self.assertEqual(_downsample(pts, 100), pts)

    def test_long_list_keeps_endpoints(self):
        pts = list(range(1000))
        out = _downsample(pts, 50)
        self.assertEqual(out[0], 0)
        self.assertEqual(out[-1], 999)
        self.assertLessEqual(len(out), 60)  # roughly target +/- small overhead

    def test_evenly_strided(self):
        pts = list(range(100))
        out = _downsample(pts, 10)
        # Ensure monotone strictly increasing
        for a, b in zip(out, out[1:]):
            self.assertLess(a, b)
        # First/last present
        self.assertEqual(out[0], 0)
        self.assertEqual(out[-1], 99)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class ScoreSeriesRouteTest(unittest.IsolatedAsyncioTestCase):
    async def _build_session(self, *, security, score_rows, decision_rows):
        session = MagicMock()
        session.get = AsyncMock(return_value=security)

        async def _execute(stmt):
            s = str(stmt).lower()
            r = MagicMock()
            if "decision_audit" in s:
                r.all = MagicMock(return_value=decision_rows)
            elif "module_scores" in s:
                scalars = MagicMock()
                scalars.__iter__ = MagicMock(return_value=iter(score_rows))
                r.scalars = MagicMock(return_value=scalars)
            else:
                r.all = MagicMock(return_value=[])
                scalars = MagicMock()
                scalars.first = MagicMock(return_value=None)
                scalars.__iter__ = MagicMock(return_value=iter([]))
                r.scalars = MagicMock(return_value=scalars)
            return r

        session.execute = AsyncMock(side_effect=_execute)
        return session

    async def test_unknown_market_rejected(self):
        session = MagicMock()
        with self.assertRaises(HTTPException) as cm:
            await score_series(
                market="zz", ticker="X", user=None, db=session,
                days=365, max_points=400,
            )
        self.assertEqual(cm.exception.status_code, 400)

    async def test_days_out_of_range_rejected(self):
        session = MagicMock()
        # Need an unknown-market check to NOT fire first. KR is valid.
        with self.assertRaises(HTTPException) as cm:
            await score_series(
                market="KR", ticker="005930", user=None, db=session,
                days=3, max_points=400,
            )
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("days", cm.exception.detail.lower())

    async def test_max_points_out_of_range_rejected(self):
        session = MagicMock()
        with self.assertRaises(HTTPException) as cm:
            await score_series(
                market="KR", ticker="005930", user=None, db=session,
                days=365, max_points=10,
            )
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("max_points", cm.exception.detail.lower())

    async def test_missing_security_returns_404(self):
        session = MagicMock()
        session.get = AsyncMock(return_value=None)
        with self.assertRaises(HTTPException) as cm:
            await score_series(
                market="KR", ticker="999999", user=None, db=session,
                days=365, max_points=400,
            )
        self.assertEqual(cm.exception.status_code, 404)

    async def test_empty_series_shape(self):
        security = SimpleNamespace(market="KR", ticker="005930", name="Samsung")
        session = await self._build_session(
            security=security, score_rows=[], decision_rows=[],
        )
        out = await score_series(
            market="KR", ticker="005930", user=None, db=session,
            days=365, max_points=400,
        )
        self.assertEqual(out.market, "KR")
        self.assertEqual(out.ticker, "005930")
        self.assertEqual(out.days, 365)
        self.assertEqual(list(out.series.keys()), ["F", "T", "I"])
        self.assertEqual(out.decision_markers, [])

    async def test_basic_series_with_decisions(self):
        security = SimpleNamespace(market="KR", ticker="005930", name="Samsung")
        # 3 score rows for module F + nothing for T / I (per-module
        # queries run separately; we just return everything to every
        # query in this mock, but the shape still asserts safely).
        now = datetime.now(UTC)
        score_rows = [
            SimpleNamespace(
                computed_ts=now - timedelta(days=i),
                score=10.0 * (i % 3 - 1), confidence=0.8,
            )
            for i in range(5)
        ]
        decision_rows = [
            (now - timedelta(days=2), "BUY", 35.0),
            (now - timedelta(days=1), "HOLD", 10.0),
        ]
        session = await self._build_session(
            security=security, score_rows=score_rows, decision_rows=decision_rows,
        )
        out = await score_series(
            market="KR", ticker="005930", user=None, db=session,
            days=365, max_points=400,
        )
        # Every per-module query reuses the same mocked rows;
        # each module list should contain 5 points.
        self.assertEqual(len(out.series["F"]), 5)
        self.assertEqual(len(out.decision_markers), 2)
        self.assertEqual(out.decision_markers[0].action, "BUY")


if __name__ == "__main__":
    unittest.main()
