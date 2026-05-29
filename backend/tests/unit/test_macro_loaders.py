"""Macro adapter + loader tests with mocked HTTP / fetcher."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-please-replace-with-32-random-bytes-x")
os.environ.setdefault("FRED_API_KEY", "fake-key")
os.environ.setdefault("BOK_ECOS_API_KEY", "fake-key")

try:
    import sqlalchemy  # noqa: F401
    import httpx  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from core.config import reset_settings_cache  # noqa: E402

    reset_settings_cache()

    from data.macro.bok_ecos import fetch_ecos_series, _parse_ecos_time  # noqa: E402
    from data.macro.fred import fetch_fred_series  # noqa: E402
    from data.macro.loader import sync_macro_series  # noqa: E402
    from data.macro.types import MacroPoint  # noqa: E402


def _fake_response(payload: dict, *, status: int = 200):
    """Build a minimal httpx.Response stand-in."""
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = lambda: None if status < 400 else (_ for _ in ()).throw(RuntimeError("status"))
    resp.json.return_value = payload
    return resp


# ──────────────────────────────────────────────────────────────────────
# FRED
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class FredAdapterTest(unittest.TestCase):
    def test_basic_parse(self):
        payload = {
            "observations": [
                {"date": "2024-01-02", "value": "5.33"},
                {"date": "2024-01-03", "value": "5.34"},
                {"date": "2024-01-04", "value": "."},   # missing — must be skipped
            ]
        }
        points = fetch_fred_series(
            "RATE_US_FFR",
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            http_get=lambda u, p: _fake_response(payload),
        )
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].series_code, "RATE_US_FFR")
        self.assertEqual(points[0].source, "fred")
        self.assertAlmostEqual(points[0].value, 5.33)

    def test_unknown_code_raises(self):
        with self.assertRaises(KeyError):
            fetch_fred_series(
                "NOT_A_REAL_CODE",
                start=date(2024, 1, 1), end=date(2024, 1, 5),
                http_get=lambda u, p: _fake_response({}),
            )

    def test_no_api_key_returns_empty(self):
        os.environ.pop("FRED_API_KEY", None)
        reset_settings_cache()
        try:
            points = fetch_fred_series(
                "RATE_US_FFR",
                start=date(2024, 1, 1), end=date(2024, 1, 5),
                http_get=lambda u, p: _fake_response({"observations": []}),
            )
            self.assertEqual(points, [])
        finally:
            os.environ["FRED_API_KEY"] = "fake-key"
            reset_settings_cache()

    def test_network_failure_returns_empty(self):
        def _boom(u, p):
            raise RuntimeError("DNS")

        points = fetch_fred_series(
            "RATE_US_FFR",
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            http_get=_boom,
        )
        self.assertEqual(points, [])


# ──────────────────────────────────────────────────────────────────────
# ECOS
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class EcosAdapterTest(unittest.TestCase):
    def test_basic_parse_daily(self):
        payload = {
            "StatisticSearch": {
                "row": [
                    {"TIME": "20240102", "DATA_VALUE": "1330.5"},
                    {"TIME": "20240103", "DATA_VALUE": "1332.0"},
                ]
            }
        }
        points = fetch_ecos_series(
            "FX_USDKRW",
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            http_get=lambda u, p: _fake_response(payload),
        )
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[0].value, 1330.5)
        self.assertEqual(points[0].ts, date(2024, 1, 2))
        self.assertEqual(points[0].source, "bok_ecos")

    def test_missing_value_skipped(self):
        payload = {
            "StatisticSearch": {
                "row": [
                    {"TIME": "20240102", "DATA_VALUE": ""},
                    {"TIME": "20240103", "DATA_VALUE": "1332.0"},
                ]
            }
        }
        points = fetch_ecos_series(
            "FX_USDKRW",
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            http_get=lambda u, p: _fake_response(payload),
        )
        self.assertEqual(len(points), 1)

    def test_time_parsing_monthly(self):
        self.assertEqual(_parse_ecos_time("202401", "M"), date(2024, 1, 1))

    def test_time_parsing_quarterly(self):
        self.assertEqual(_parse_ecos_time("2024Q1", "Q"), date(2024, 1, 1))
        self.assertEqual(_parse_ecos_time("2024Q3", "Q"), date(2024, 7, 1))


# ──────────────────────────────────────────────────────────────────────
# Loader
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class MacroLoaderTest(unittest.TestCase):
    def _session(self):
        s = MagicMock()
        s.scalars.return_value.first.return_value = None
        return s

    def test_processes_multiple_series(self):
        def _fetcher(code, start, end):
            return [
                MacroPoint(series_code=code, ts=start, value=1.0,
                           source="mock", as_of_ts=datetime.now(UTC)),
            ]

        report = sync_macro_series(
            self._session(),
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            series_codes=["RATE_US_FFR", "FX_USDKRW"],
            fetcher=_fetcher,
        )
        self.assertEqual(report.series_processed, 2)
        self.assertEqual(report.series_failed, 0)
        self.assertEqual(report.rows_upserted, 2)

    def test_one_failure_does_not_stop_others(self):
        def _fetcher(code, start, end):
            if code == "BAD":
                raise RuntimeError("oops")
            return [MacroPoint(series_code=code, ts=start, value=1.0,
                               source="mock", as_of_ts=datetime.now(UTC))]

        report = sync_macro_series(
            self._session(),
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            series_codes=["RATE_US_FFR", "BAD", "VIX"],
            fetcher=_fetcher,
        )
        self.assertEqual(report.series_processed, 2)
        self.assertEqual(report.series_failed, 1)
        self.assertEqual(len(report.errors), 1)


if __name__ == "__main__":
    unittest.main()
