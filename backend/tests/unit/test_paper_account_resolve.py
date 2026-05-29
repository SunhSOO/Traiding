"""Tests for the per-market paper account resolver.

The resolver picks ``default-kr`` / ``default-us`` when the caller
didn't specify an account, based on the ``market`` query. Pure
function — no DB.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

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

    from routes.paper import _resolve_account_name  # noqa: E402


@unittest.skipUnless(_HAVE_DEPS, "deps")
class ResolveAccountNameTest(unittest.TestCase):
    def test_explicit_name_wins(self):
        # An explicit operator-renamed account stays untouched even
        # when market is given.
        self.assertEqual(
            _resolve_account_name("my-experiment", "KR"),
            "my-experiment",
        )

    def test_default_alias_routes_to_market(self):
        self.assertEqual(_resolve_account_name("default", "KR"), "default-kr")
        self.assertEqual(_resolve_account_name("default", "US"), "default-us")

    def test_none_routes_to_market(self):
        self.assertEqual(_resolve_account_name(None, "KR"), "default-kr")
        self.assertEqual(_resolve_account_name(None, "US"), "default-us")

    def test_case_insensitive_market(self):
        self.assertEqual(_resolve_account_name(None, "kr"), "default-kr")
        self.assertEqual(_resolve_account_name(None, "us"), "default-us")

    def test_no_market_falls_back_to_kr(self):
        # Conservative "pick something" choice. UI should pass market
        # explicitly; this only kicks in on raw curl calls.
        self.assertEqual(_resolve_account_name(None, None), "default-kr")
        self.assertEqual(_resolve_account_name("default", None), "default-kr")


if __name__ == "__main__":
    unittest.main()
