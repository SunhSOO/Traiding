"""Admin route tests — scheduler pause/resume/state + kill-switch path.

The kill-switch test focuses on the orchestration (scheduler pause +
report assembly) rather than the broker close-position internals,
which have their own tests under ``test_paper_broker.py``."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime
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

    from routes.admin import (  # noqa: E402
        kill_switch, pause_scheduler, resume_scheduler, scheduler_state,
    )


def _make_user():
    return SimpleNamespace(username="operator")


def _make_request(scheduler):
    """Build a Request-like object exposing ``request.app.state.scheduler``."""
    state = SimpleNamespace(scheduler=scheduler)
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app)


def _empty_async_session():
    """An AsyncSession that returns no PaperAccount."""
    session = MagicMock()

    async def _execute(stmt):
        r = MagicMock()
        scalars = MagicMock()
        scalars.first = MagicMock(return_value=None)
        scalars.__iter__ = MagicMock(return_value=iter([]))
        r.scalars = MagicMock(return_value=scalars)
        return r

    session.execute = AsyncMock(side_effect=_execute)
    return session


# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps")
class SchedulerStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_scheduler_returns_empty_state(self):
        out = await scheduler_state(user=_make_user(), request=_make_request(None))
        self.assertFalse(out.started)
        self.assertFalse(out.paused)
        self.assertEqual(out.job_count, 0)
        self.assertEqual(out.jobs, [])

    async def test_started_running_state(self):
        sched = MagicMock()
        sched.status = MagicMock(return_value=[
            {"id": "prices.kr.daily", "next_run": "2026-05-28T06:35:00+00:00"},
            {"id": "prices.us.daily", "next_run": "2026-05-28T21:05:00+00:00"},
        ])
        sched.is_paused = MagicMock(return_value=False)
        out = await scheduler_state(user=_make_user(), request=_make_request(sched))
        self.assertTrue(out.started)
        self.assertFalse(out.paused)
        self.assertEqual(out.job_count, 2)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class PauseResumeTest(unittest.IsolatedAsyncioTestCase):
    async def test_pause_without_scheduler_raises_503(self):
        with self.assertRaises(HTTPException) as cm:
            await pause_scheduler(user=_make_user(), request=_make_request(None))
        self.assertEqual(cm.exception.status_code, 503)

    async def test_pause_calls_scheduler_pause_all(self):
        sched = MagicMock()
        sched.pause_all = MagicMock(return_value=7)
        out = await pause_scheduler(user=_make_user(), request=_make_request(sched))
        self.assertTrue(out.paused)
        self.assertEqual(out.jobs_touched, 7)
        sched.pause_all.assert_called_once()

    async def test_resume_calls_scheduler_resume_all(self):
        sched = MagicMock()
        sched.resume_all = MagicMock(return_value=7)
        out = await resume_scheduler(user=_make_user(), request=_make_request(sched))
        self.assertFalse(out.paused)
        self.assertEqual(out.jobs_touched, 7)
        sched.resume_all.assert_called_once()


@unittest.skipUnless(_HAVE_DEPS, "deps")
class KillSwitchTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_account_no_positions_still_succeeds(self):
        """Operator pressed kill but no paper account exists yet.
        Should pause scheduler + return a clean report, no crash."""
        sched = MagicMock()
        sched.pause_all = MagicMock(return_value=3)

        session = _empty_async_session()

        out = await kill_switch(
            user=_make_user(),
            request=_make_request(sched),
            db=session,
            account_name="default",
        )
        self.assertEqual(out.triggered_by, "operator")
        self.assertEqual(out.scheduler_jobs_paused, 3)
        self.assertTrue(out.scheduler_paused)
        self.assertEqual(out.positions_closed, 0)
        self.assertEqual(out.close_errors, [])
        # triggered_at parses as ISO timestamp
        datetime.fromisoformat(out.triggered_at)

    async def test_no_scheduler_does_not_block_close(self):
        """Even if scheduler is missing, the kill switch should still
        attempt to close positions (graceful — emergency must always run)."""
        session = _empty_async_session()
        out = await kill_switch(
            user=_make_user(),
            request=_make_request(None),
            db=session,
            account_name="default",
        )
        self.assertFalse(out.scheduler_paused)
        self.assertEqual(out.scheduler_jobs_paused, 0)
        self.assertEqual(out.positions_closed, 0)


if __name__ == "__main__":
    unittest.main()
