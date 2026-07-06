"""Scheduler tests — declarative job spec inspection + manual trigger.

We don't start APScheduler in unit tests (event-loop side effects);
those go in tests/integration/test_scheduler_run.py later.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


try:
    from runtime.scheduler import DEFAULT_JOBS, JobSpec, WoonamScheduler

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


@unittest.skipUnless(_AVAILABLE, "runtime.scheduler imports blocked by missing deps")
class DefaultJobsTest(unittest.TestCase):
    def test_default_jobs_present(self):
        ids = {j.id for j in DEFAULT_JOBS}
        self.assertEqual(
            ids,
            {
                "universe.monthly",
                "prices.kr.daily",
                "prices.us.daily",
                "macro.daily",
                "disclosures.kr.daily",
                "disclosures.us.daily",
                "financials.monthly",
                "news.intraday.rss",
                "news.daily.kr",
                "news.daily.global",
                "information.classify.hourly",
                "information.score.daily",
                "technical.score.daily",
                "fundamental.score.weekly",
                "decisions.daily",
                "ml_decisions.daily",
                "features.rebuild.daily",
                "integrated.daily",
                "training.weekly",
                "regime.daily",
                "backtest.walk_forward.weekly",
                "mlops.retrain.weekly",
                "mlops.drift_check.daily",
                "news.backfill.nightly",
                "news.gdelt_history.monthly",
                "fundamentals.forward_estimates.weekly",
            },
        )

    def test_kr_prices_runs_on_kst_weekday_close(self):
        spec = next(j for j in DEFAULT_JOBS if j.id == "prices.kr.daily")
        self.assertEqual(spec.timezone, "Asia/Seoul")
        self.assertEqual(spec.cron_kwargs["hour"], 15)
        self.assertEqual(spec.cron_kwargs["minute"], 35)
        self.assertEqual(spec.cron_kwargs["day_of_week"], "mon-fri")

    def test_us_prices_runs_on_est_weekday_close(self):
        spec = next(j for j in DEFAULT_JOBS if j.id == "prices.us.daily")
        self.assertEqual(spec.timezone, "America/New_York")
        self.assertEqual(spec.cron_kwargs["hour"], 16)
        self.assertEqual(spec.cron_kwargs["minute"], 5)


@unittest.skipUnless(_AVAILABLE, "deps not installed")
class WoonamSchedulerTest(unittest.TestCase):
    def test_status_before_start_lists_jobs(self):
        sched = WoonamScheduler()
        rows = sched.status()
        ids = {r["id"] for r in rows}
        for j in DEFAULT_JOBS:
            self.assertIn(j.id, ids)
        # All have next_run=None before start
        for r in rows:
            self.assertIsNone(r["next_run"])

    def test_run_job_now_dispatches_to_named_spec(self):
        ran = {"called": 0}

        async def _task():
            ran["called"] += 1

        sched = WoonamScheduler(jobs=[
            JobSpec(id="mock", func=_task, trigger="cron", cron_kwargs={"hour": 0})
        ])
        asyncio.run(sched.run_job_now("mock"))
        self.assertEqual(ran["called"], 1)

    def test_run_unknown_job_raises(self):
        sched = WoonamScheduler(jobs=[])
        with self.assertRaises(KeyError):
            asyncio.run(sched.run_job_now("nope"))


if __name__ == "__main__":
    unittest.main()
