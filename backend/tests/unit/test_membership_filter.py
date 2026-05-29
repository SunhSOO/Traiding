"""Tests for the in-memory point-in-time membership filter.

Pure data-structure test — does not hit the DB. The ``from_db``
helper is exercised by integration tests."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-please-replace-with-32-random-bytes-x")

try:
    import sqlalchemy  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False

if _HAVE_DEPS:
    from data.universe.membership_query import MembershipFilter  # noqa: E402


@unittest.skipUnless(_HAVE_DEPS, "deps")
class MembershipFilterTest(unittest.TestCase):
    def _build(self):
        # AAA was in the index 2024 → continues; BBB joined 2025-03;
        # CCC was in 2023 only.
        return MembershipFilter(_ranges={
            "AAA": [(date(2024, 1, 1), None)],
            "BBB": [(date(2025, 3, 15), None)],
            "CCC": [(date(2023, 1, 1), date(2023, 12, 31))],
        })

    def test_active_continuous(self):
        f = self._build()
        self.assertTrue(f.is_member("AAA", date(2024, 6, 1)))
        self.assertTrue(f.is_member("AAA", date(2025, 5, 27)))

    def test_pre_join_date(self):
        f = self._build()
        self.assertFalse(f.is_member("BBB", date(2025, 3, 14)))
        self.assertTrue(f.is_member("BBB", date(2025, 3, 15)))

    def test_post_leave_date(self):
        f = self._build()
        self.assertTrue(f.is_member("CCC", date(2023, 6, 1)))
        # valid_to is exclusive (membership ends ON that day)
        self.assertFalse(f.is_member("CCC", date(2023, 12, 31)))
        self.assertFalse(f.is_member("CCC", date(2024, 1, 1)))

    def test_unknown_ticker(self):
        f = self._build()
        self.assertFalse(f.is_member("ZZZ", date(2024, 6, 1)))

    def test_members_at_returns_subset(self):
        f = self._build()
        self.assertEqual(f.members_at(date(2023, 6, 1)), {"CCC"})
        self.assertEqual(f.members_at(date(2024, 6, 1)), {"AAA"})
        self.assertEqual(f.members_at(date(2025, 5, 27)), {"AAA", "BBB"})

    def test_multiple_ranges_for_one_ticker(self):
        # A ticker that left and rejoined later
        f = MembershipFilter(_ranges={
            "XYZ": [
                (date(2022, 1, 1), date(2023, 6, 30)),
                (date(2024, 4, 1), None),
            ],
        })
        self.assertTrue(f.is_member("XYZ", date(2022, 5, 1)))
        self.assertFalse(f.is_member("XYZ", date(2023, 9, 1)))
        self.assertTrue(f.is_member("XYZ", date(2024, 5, 1)))


if __name__ == "__main__":
    unittest.main()
