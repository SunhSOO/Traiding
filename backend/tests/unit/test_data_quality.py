"""Pure data-quality tests — no DB."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.data_quality import (  # noqa: E402
    find_gaps, is_weekday, score_freshness,
)


class GapDetectionTest(unittest.TestCase):
    def test_no_gaps_when_every_day_present(self):
        # Mon-Fri week: 2026-05-25 ~ 2026-05-29
        actual = [date(2026, 5, d) for d in (25, 26, 27, 28, 29)]
        out = find_gaps(
            market="KR", ticker="X",
            actual_dates=actual,
            is_trading_day=is_weekday,
            start=date(2026, 5, 25), end=date(2026, 5, 29),
            min_gap_days=1,
        )
        self.assertEqual(out, [])

    def test_single_missing_day_caught_at_min1(self):
        # Wed missing
        actual = [date(2026, 5, 25), date(2026, 5, 26), date(2026, 5, 28), date(2026, 5, 29)]
        out = find_gaps(
            market="KR", ticker="X",
            actual_dates=actual,
            is_trading_day=is_weekday,
            start=date(2026, 5, 25), end=date(2026, 5, 29),
            min_gap_days=1,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].start, date(2026, 5, 27))
        self.assertEqual(out[0].end, date(2026, 5, 27))
        self.assertEqual(out[0].days, 1)

    def test_single_missing_filtered_by_min2(self):
        actual = [date(2026, 5, 25), date(2026, 5, 26), date(2026, 5, 28), date(2026, 5, 29)]
        out = find_gaps(
            market="KR", ticker="X",
            actual_dates=actual,
            is_trading_day=is_weekday,
            start=date(2026, 5, 25), end=date(2026, 5, 29),
            min_gap_days=2,
        )
        self.assertEqual(out, [])  # 1-day gap below threshold

    def test_weekends_ignored(self):
        # Full work-week + weekend, all weekdays present — no gaps
        actual = [date(2026, 5, d) for d in (25, 26, 27, 28, 29)]
        out = find_gaps(
            market="KR", ticker="X",
            actual_dates=actual,
            is_trading_day=is_weekday,
            start=date(2026, 5, 25), end=date(2026, 5, 31),  # spans weekend
            min_gap_days=1,
        )
        self.assertEqual(out, [])

    def test_trailing_gap_captured(self):
        # Last 2 days missing
        actual = [date(2026, 5, 25), date(2026, 5, 26), date(2026, 5, 27)]
        out = find_gaps(
            market="KR", ticker="X",
            actual_dates=actual,
            is_trading_day=is_weekday,
            start=date(2026, 5, 25), end=date(2026, 5, 29),
            min_gap_days=1,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].start, date(2026, 5, 28))
        self.assertEqual(out[0].end, date(2026, 5, 29))
        self.assertEqual(out[0].days, 2)

    def test_multiple_gaps(self):
        # Mon present, Tue gap, Wed gap, Thu present, Fri gap
        actual = [date(2026, 5, 25), date(2026, 5, 28)]
        out = find_gaps(
            market="KR", ticker="X",
            actual_dates=actual,
            is_trading_day=is_weekday,
            start=date(2026, 5, 25), end=date(2026, 5, 29),
            min_gap_days=1,
        )
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].days, 2)  # Tue+Wed
        self.assertEqual(out[1].days, 1)  # Fri


class FreshnessScoreTest(unittest.TestCase):
    def test_fresh_within_threshold(self):
        fs = score_freshness(
            market="KR", ticker="005930",
            last_price_date=date(2026, 5, 26),
            today=date(2026, 5, 27),
        )
        self.assertEqual(fs.tier, "FRESH")
        self.assertEqual(fs.days_since_last, 1)

    def test_stale_after_threshold(self):
        fs = score_freshness(
            market="KR", ticker="005930",
            last_price_date=date(2026, 5, 20),
            today=date(2026, 5, 27),
        )
        # KR threshold_stale=4 → 7 days = STALE
        self.assertEqual(fs.tier, "STALE")

    def test_dead_long_gap(self):
        fs = score_freshness(
            market="KR", ticker="005930",
            last_price_date=date(2026, 1, 1),
            today=date(2026, 5, 27),
        )
        self.assertEqual(fs.tier, "DEAD")

    def test_no_data_dead(self):
        fs = score_freshness(
            market="US", ticker="AAPL",
            last_price_date=None,
            today=date(2026, 5, 27),
        )
        self.assertEqual(fs.tier, "DEAD")
        self.assertIsNone(fs.days_since_last)


if __name__ == "__main__":
    unittest.main()
