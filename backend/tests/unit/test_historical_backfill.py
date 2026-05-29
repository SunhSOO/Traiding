"""Pure tests for the historical news backfill orchestrator.

No DB / network — adapters are stub callables. Covers:

- Month chunking inclusivity at boundaries
- Resume skips DONE chunks
- Rate limiter math
- Runner skips unknown sources, records errors
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import structlog  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False

if _HAVE_DEPS:
    from data.news.historical_backfill import (  # noqa: E402
        BackfillChunk, BackfillProgressRow, ChunkResult, RateLimiter,
        TickerSpec, _months_in_range, plan_chunks, run_chunks,
    )
    from data.news.types import NewsArticleRow  # noqa: E402


# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps")
class MonthRangeTest(unittest.TestCase):
    def test_single_month(self):
        out = list(_months_in_range(date(2026, 5, 1), date(2026, 5, 31)))
        self.assertEqual(len(out), 1)
        period, s, e = out[0]
        self.assertEqual(period, "2026-05")
        self.assertEqual(s, date(2026, 5, 1))
        self.assertEqual(e, date(2026, 5, 31))

    def test_three_months_clipped(self):
        # Range starts mid-Apr → first chunk clipped, last clipped too
        out = list(_months_in_range(date(2026, 4, 15), date(2026, 6, 10)))
        self.assertEqual([o[0] for o in out], ["2026-04", "2026-05", "2026-06"])
        self.assertEqual(out[0][1], date(2026, 4, 15))
        self.assertEqual(out[0][2], date(2026, 4, 30))
        self.assertEqual(out[1][1], date(2026, 5, 1))
        self.assertEqual(out[1][2], date(2026, 5, 31))
        self.assertEqual(out[2][1], date(2026, 6, 1))
        self.assertEqual(out[2][2], date(2026, 6, 10))

    def test_year_rollover(self):
        out = list(_months_in_range(date(2026, 12, 15), date(2027, 1, 10)))
        self.assertEqual([o[0] for o in out], ["2026-12", "2027-01"])

    def test_end_before_start_yields_nothing(self):
        out = list(_months_in_range(date(2026, 5, 30), date(2026, 5, 10)))
        self.assertEqual(out, [])


@unittest.skipUnless(_HAVE_DEPS, "deps")
class PlanChunksTest(unittest.TestCase):
    def _tickers(self):
        return [
            TickerSpec(market="KR", ticker="005930", query="삼성전자"),
            TickerSpec(market="US", ticker="AAPL", query="Apple Inc"),
        ]

    def test_cross_product(self):
        chunks = plan_chunks(
            job_id="J1",
            tickers=self._tickers(),
            sources=["bigkinds", "gdelt"],
            start=date(2026, 1, 1), end=date(2026, 2, 28),
        )
        # 2 tickers × 2 sources × 2 months = 8 chunks
        self.assertEqual(len(chunks), 8)

    def test_done_chunks_skipped(self):
        existing = [
            BackfillProgressRow(
                source="bigkinds", market="KR", ticker="005930",
                period="2026-01", status="done", rows_inserted=42,
            ),
        ]
        chunks = plan_chunks(
            job_id="J1",
            tickers=self._tickers(),
            sources=["bigkinds", "gdelt"],
            start=date(2026, 1, 1), end=date(2026, 1, 31),
            existing=existing,
        )
        # 2 tickers × 2 sources × 1 month = 4, minus 1 done = 3
        self.assertEqual(len(chunks), 3)
        # Verify the right one was dropped
        for c in chunks:
            self.assertFalse(
                c.source == "bigkinds" and c.market == "KR"
                and c.ticker == "005930" and c.period == "2026-01"
            )

    def test_error_status_retried(self):
        # Same key as done test but with status=error → still in plan
        existing = [
            BackfillProgressRow(
                source="bigkinds", market="KR", ticker="005930",
                period="2026-01", status="error",
            ),
        ]
        chunks = plan_chunks(
            job_id="J1",
            tickers=self._tickers(),
            sources=["bigkinds"],
            start=date(2026, 1, 1), end=date(2026, 1, 31),
            existing=existing,
        )
        # Both tickers still emit chunks
        self.assertEqual(len(chunks), 2)

    def test_end_before_start_raises(self):
        with self.assertRaises(ValueError):
            plan_chunks(
                job_id="J1", tickers=self._tickers(), sources=["bigkinds"],
                start=date(2026, 5, 31), end=date(2026, 5, 1),
            )


@unittest.skipUnless(_HAVE_DEPS, "deps")
class RateLimiterTest(unittest.TestCase):
    def test_first_call_no_wait(self):
        rl = RateLimiter(min_interval_seconds={"bigkinds": 1.5})
        self.assertEqual(rl.wait_seconds_for("bigkinds", now=100.0), 0.0)

    def test_subsequent_call_waits(self):
        rl = RateLimiter(min_interval_seconds={"bigkinds": 1.5})
        rl.mark("bigkinds", now=100.0)
        self.assertAlmostEqual(rl.wait_seconds_for("bigkinds", now=100.5), 1.0)
        self.assertAlmostEqual(rl.wait_seconds_for("bigkinds", now=101.0), 0.5)
        # Past interval → no wait
        self.assertEqual(rl.wait_seconds_for("bigkinds", now=102.0), 0.0)

    def test_unknown_source_no_wait(self):
        rl = RateLimiter(min_interval_seconds={"bigkinds": 1.5})
        rl.mark("bigkinds", now=100.0)
        self.assertEqual(rl.wait_seconds_for("naver", now=100.0), 0.0)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class RunChunksTest(unittest.TestCase):
    def _make_chunks(self, source: str, count: int) -> list[BackfillChunk]:
        return [
            BackfillChunk(
                job_id="J1", source=source, market="KR", ticker="005930",
                period=f"2026-{i:02d}", query="삼성전자",
                start=datetime(2026, i, 1, tzinfo=UTC),
                end=datetime(2026, i, 28, tzinfo=UTC),
            )
            for i in range(1, count + 1)
        ]

    def test_happy_path(self):
        chunks = self._make_chunks("bigkinds", 3)
        calls = []
        statuses = []

        def adapter(chunk):
            calls.append((chunk.source, chunk.period))
            return [
                NewsArticleRow(
                    source=chunk.source, title=f"t{chunk.period}",
                    published_ts=chunk.start, language="ko",
                )
            ]

        def persist(chunk, rows):
            return len(rows)

        def update_status(chunk, status, inserted, error):
            statuses.append((chunk.period, status, inserted, error))

        results = run_chunks(
            chunks,
            adapters={"bigkinds": adapter},
            persist=persist,
            update_status=update_status,
            limiter=RateLimiter(min_interval_seconds={}),
            sleeper=lambda s: None,
            now_fn=lambda: 0.0,
        )
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual([r.rows_inserted for r in results], [1, 1, 1])
        # Each chunk has 2 status updates: in_progress, then done
        self.assertEqual(len(statuses), 6)
        # Final state for each was 'done'
        done_states = [s for s in statuses if s[1] == "done"]
        self.assertEqual(len(done_states), 3)

    def test_unknown_source_records_error(self):
        chunks = self._make_chunks("phantom", 1)
        statuses = []
        results = run_chunks(
            chunks,
            adapters={"bigkinds": lambda c: []},
            persist=lambda c, r: 0,
            update_status=lambda c, s, i, e: statuses.append((s, e)),
            limiter=RateLimiter(min_interval_seconds={}),
            sleeper=lambda s: None,
            now_fn=lambda: 0.0,
        )
        self.assertFalse(results[0].ok)
        self.assertIn("unknown", results[0].error.lower())
        # Status updater was called once with "error"
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0][0], "error")

    def test_adapter_exception_recorded(self):
        chunks = self._make_chunks("bigkinds", 2)

        def bad_adapter(chunk):
            raise RuntimeError("upstream down")

        results = run_chunks(
            chunks,
            adapters={"bigkinds": bad_adapter},
            persist=lambda c, r: 0,
            update_status=lambda c, s, i, e: None,
            limiter=RateLimiter(min_interval_seconds={}),
            sleeper=lambda s: None,
            now_fn=lambda: 0.0,
        )
        self.assertEqual(len(results), 2)
        self.assertFalse(any(r.ok for r in results))
        self.assertIn("upstream down", results[0].error)

    def test_max_chunks_caps_invocation(self):
        chunks = self._make_chunks("bigkinds", 5)
        results = run_chunks(
            chunks,
            adapters={"bigkinds": lambda c: []},
            persist=lambda c, r: 0,
            update_status=lambda c, s, i, e: None,
            limiter=RateLimiter(min_interval_seconds={}),
            sleeper=lambda s: None,
            now_fn=lambda: 0.0,
            max_chunks=2,
        )
        self.assertEqual(len(results), 2)

    def test_rate_limiter_sleeps_between_calls(self):
        chunks = self._make_chunks("bigkinds", 3)
        sleep_calls = []
        clock = [0.0]

        def now_fn():
            return clock[0]

        def sleeper(s):
            sleep_calls.append(s)
            clock[0] += s  # advance virtual clock

        run_chunks(
            chunks,
            adapters={"bigkinds": lambda c: []},
            persist=lambda c, r: 0,
            update_status=lambda c, s, i, e: None,
            limiter=RateLimiter(min_interval_seconds={"bigkinds": 1.5}),
            sleeper=sleeper,
            now_fn=now_fn,
        )
        # First call: no sleep (last_call_ts unset).
        # 2nd and 3rd: should each sleep ~1.5s.
        self.assertEqual(len(sleep_calls), 2)
        self.assertAlmostEqual(sleep_calls[0], 1.5)


if __name__ == "__main__":
    unittest.main()
