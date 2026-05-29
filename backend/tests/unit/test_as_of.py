"""Tests for the look-ahead bias guard.

These are the most safety-critical tests in the system. If any of
them regresses, signals can silently use future data and the model
trained on the result will fail in live trading.
"""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.as_of import AsOfContext, AsOfError, now, require_as_of  # noqa: E402


class AsOfValidationTest(unittest.TestCase):
    def test_decorator_rejects_missing_as_of(self):
        @require_as_of
        def fetch(symbol: str, *, as_of: datetime) -> str:
            return f"{symbol}@{as_of.isoformat()}"

        with self.assertRaises(AsOfError):
            fetch("AAPL")  # type: ignore[call-arg]

    def test_decorator_rejects_none(self):
        @require_as_of
        def fetch(*, as_of: datetime) -> None:
            return None

        with self.assertRaises(AsOfError):
            fetch(as_of=None)  # type: ignore[arg-type]

    def test_decorator_rejects_naive_datetime(self):
        @require_as_of
        def fetch(*, as_of: datetime) -> None:
            return None

        naive = datetime(2026, 1, 1, 12, 0, 0)  # no tzinfo
        with self.assertRaises(AsOfError) as cm:
            fetch(as_of=naive)
        self.assertIn("timezone-aware", str(cm.exception))

    def test_decorator_rejects_future_timestamp(self):
        @require_as_of
        def fetch(*, as_of: datetime) -> None:
            return None

        future = datetime.now(UTC) + timedelta(hours=1)
        with self.assertRaises(AsOfError) as cm:
            fetch(as_of=future)
        self.assertIn("future", str(cm.exception))

    def test_decorator_rejects_non_datetime(self):
        @require_as_of
        def fetch(*, as_of: datetime) -> None:
            return None

        with self.assertRaises(AsOfError):
            fetch(as_of="2026-01-01")  # type: ignore[arg-type]

    def test_decorator_accepts_valid_utc(self):
        @require_as_of
        def fetch(symbol: str, *, as_of: datetime) -> str:
            return f"{symbol}@{as_of.year}"

        ts = datetime(2025, 1, 1, tzinfo=UTC)
        self.assertEqual(fetch("AAPL", as_of=ts), "AAPL@2025")

    def test_decorator_accepts_other_timezones(self):
        @require_as_of
        def fetch(*, as_of: datetime) -> int:
            return as_of.year

        kst = timezone(timedelta(hours=9))
        ts = datetime(2025, 6, 15, 9, 0, 0, tzinfo=kst)
        self.assertEqual(fetch(as_of=ts), 2025)

    def test_decorator_requires_as_of_in_signature(self):
        with self.assertRaises(TypeError):
            @require_as_of
            def bad(symbol: str) -> str:  # type: ignore[unused-ignore]
                return symbol


class AsOfContextTest(unittest.TestCase):
    def test_context_pins_now(self):
        pinned = datetime(2020, 3, 15, 9, 30, tzinfo=UTC)
        wall_before = datetime.now(UTC)
        self.assertNotEqual(now().date(), pinned.date())

        with AsOfContext(pinned):
            self.assertEqual(now(), pinned)

        wall_after = now()
        self.assertGreaterEqual(wall_after, wall_before)

    def test_context_must_be_tz_aware(self):
        with self.assertRaises(AsOfError):
            AsOfContext(datetime(2020, 1, 1))

    def test_context_nesting(self):
        outer = datetime(2020, 1, 1, tzinfo=UTC)
        inner = datetime(2021, 1, 1, tzinfo=UTC)
        with AsOfContext(outer):
            self.assertEqual(now(), outer)
            with AsOfContext(inner):
                self.assertEqual(now(), inner)
            self.assertEqual(now(), outer)

    def test_context_isolates_exceptions(self):
        pinned = datetime(2020, 1, 1, tzinfo=UTC)
        try:
            with AsOfContext(pinned):
                self.assertEqual(now(), pinned)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # After exception, the pin must have been restored to None.
        self.assertGreater(now().year, 2020)

    def test_context_pin_visible_through_decorator(self):
        """End-to-end: a function decorated with @require_as_of can
        accept an explicit `as_of` that came from `now()` inside a
        pinned context — this is how backtests work."""
        @require_as_of
        def fetch(*, as_of: datetime) -> int:
            return as_of.year

        pinned = datetime(2022, 6, 1, tzinfo=UTC)
        with AsOfContext(pinned):
            ts = now()
            self.assertEqual(fetch(as_of=ts), 2022)


if __name__ == "__main__":
    unittest.main()
