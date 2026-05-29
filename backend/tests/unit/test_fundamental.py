"""Fundamental adapters + loader + concept taxonomy tests.

External calls (OpenDartReader, SEC HTTP) are mocked. DB integration
tests deferred.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-please-replace-with-32-random-bytes-x")
os.environ.setdefault("SEC_USER_AGENT", "test-bot test@example.com")
os.environ.setdefault("DART_API_KEY", "fake-key")

try:
    import sqlalchemy  # noqa: F401
    import httpx  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from core.config import reset_settings_cache  # noqa: E402

    reset_settings_cache()

    from core.types import Market  # noqa: E402
    from data.fundamental.concepts import (  # noqa: E402
        REGISTRY,
        kr_concept_for,
        required_concepts_for_ratios,
        us_concept_for,
    )
    from data.fundamental.kr_dart import fetch_kr_financials  # noqa: E402
    from data.fundamental.loader import sync_financials  # noqa: E402
    from data.fundamental.types import FinancialFactRow  # noqa: E402
    from data.fundamental.us_edgar import fetch_us_financials  # noqa: E402


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class ConceptTaxonomyTest(unittest.TestCase):
    def test_canonical_concepts_present(self):
        for code in ("REVENUE", "NET_INCOME", "TOTAL_ASSETS", "CFO"):
            self.assertIn(code, REGISTRY)

    def test_kr_account_lookup(self):
        self.assertEqual(kr_concept_for("ifrs-full_Revenue"), "REVENUE")
        self.assertEqual(kr_concept_for("매출원가"), "COGS")
        self.assertIsNone(kr_concept_for("UNKNOWN_TAG"))

    def test_us_tag_lookup(self):
        self.assertEqual(us_concept_for("Revenues"), "REVENUE")
        self.assertEqual(us_concept_for("EarningsPerShareBasic"), "EPS_BASIC")
        self.assertIsNone(us_concept_for("NonexistentTag"))

    def test_ratio_minimum_set(self):
        req = required_concepts_for_ratios()
        # Phase 2 ratios depend on at least these
        for must in ("REVENUE", "NET_INCOME", "TOTAL_ASSETS", "TOTAL_EQUITY", "CFO"):
            self.assertIn(must, req)


# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class KrFinancialsAdapterTest(unittest.TestCase):
    def test_basic_parse_quarterly(self):
        # Simulated DART finstate_all output for Samsung 2024-Q1
        rows = [
            {"account_id": "ifrs-full_Revenue", "account_nm": "매출액",
             "thstrm_amount": "71924856000000", "currency": "KRW", "rcept_dt": "20240430"},
            {"account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익",
             "thstrm_amount": "6605815000000", "currency": "KRW"},
            {"account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
             "account_nm": "당기순이익", "thstrm_amount": "6755901000000",
             "currency": "KRW"},
            # Garbage / unmapped row — must be skipped
            {"account_id": "ifrs-full_OtherWhatever", "thstrm_amount": "999"},
        ]
        facts = fetch_kr_financials(
            corp_code="00126380", ticker="005930",
            year=2024, period_kind="Q1",
            fetch_finstate=lambda *a: rows,
        )
        by_concept = {f.concept: f for f in facts}
        self.assertIn("REVENUE", by_concept)
        self.assertIn("OPERATING_INCOME", by_concept)
        self.assertAlmostEqual(by_concept["REVENUE"].value, 71924856000000)
        self.assertEqual(by_concept["REVENUE"].period_end, date(2024, 3, 31))
        self.assertEqual(by_concept["REVENUE"].period_kind, "Q")
        self.assertEqual(by_concept["REVENUE"].source, "dart")
        # as_of from rcept_dt
        self.assertEqual(by_concept["REVENUE"].as_of_ts.year, 2024)
        self.assertEqual(by_concept["REVENUE"].as_of_ts.month, 4)

    def test_annual_period_end_is_year_end(self):
        rows = [{"account_id": "ifrs-full_Revenue", "thstrm_amount": "1"}]
        facts = fetch_kr_financials(
            corp_code="00126380", ticker="005930",
            year=2023, period_kind="ANNUAL",
            fetch_finstate=lambda *a: rows,
        )
        self.assertEqual(facts[0].period_end, date(2023, 12, 31))
        self.assertEqual(facts[0].period_kind, "A")

    def test_skips_unparseable_value(self):
        rows = [{"account_id": "ifrs-full_Revenue", "thstrm_amount": "n/a"}]
        facts = fetch_kr_financials(
            corp_code="X", ticker="005930", year=2024, period_kind="Q1",
            fetch_finstate=lambda *a: rows,
        )
        self.assertEqual(facts, [])

    def test_first_match_per_concept_wins(self):
        # If both Revenue and another mapped synonym appear, we keep one.
        rows = [
            {"account_id": "ifrs-full_Revenue", "thstrm_amount": "100"},
            {"account_id": "ifrs_Revenue", "thstrm_amount": "999"},  # synonym
        ]
        facts = fetch_kr_financials(
            corp_code="X", ticker="005930", year=2024, period_kind="Q1",
            fetch_finstate=lambda *a: rows,
        )
        revenue = [f for f in facts if f.concept == "REVENUE"]
        self.assertEqual(len(revenue), 1)
        self.assertAlmostEqual(revenue[0].value, 100)

    def test_invalid_period_kind_raises(self):
        with self.assertRaises(ValueError):
            fetch_kr_financials(
                corp_code="X", ticker="005930", year=2024, period_kind="Q4",
                fetch_finstate=lambda *a: [],
            )

    def test_fetcher_exception_returns_empty(self):
        def _boom(*a, **kw):
            raise RuntimeError("DART unreachable")

        facts = fetch_kr_financials(
            corp_code="X", ticker="005930", year=2024, period_kind="Q1",
            fetch_finstate=_boom,
        )
        self.assertEqual(facts, [])


# ──────────────────────────────────────────────────────────────────────


def _edgar_response(payload: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = lambda: None if status < 400 else (_ for _ in ()).throw(RuntimeError("status"))
    resp.json.return_value = payload
    return resp


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class UsFinancialsAdapterTest(unittest.TestCase):
    def test_basic_parse_with_10q_and_10k(self):
        # AAPL Revenues — two example facts
        payload = {
            "units": {
                "USD": [
                    {"start": "2023-01-01", "end": "2023-03-31",
                     "val": 94836000000, "fp": "Q1", "fy": 2023,
                     "form": "10-Q", "filed": "2023-05-05"},
                    {"start": "2022-10-01", "end": "2023-09-30",
                     "val": 383285000000, "fp": "FY", "fy": 2023,
                     "form": "10-K", "filed": "2023-11-03"},
                ]
            }
        }

        calls = {"n": 0}
        def _fake_get(url, headers):
            calls["n"] += 1
            return _edgar_response(payload)

        facts = fetch_us_financials(
            cik="320193", ticker="AAPL",
            concepts=["REVENUE"],
            http_get=_fake_get,
        )
        self.assertEqual(len(facts), 2)
        kinds = {f.period_kind for f in facts}
        self.assertEqual(kinds, {"Q", "A"})
        annual = next(f for f in facts if f.period_kind == "A")
        self.assertEqual(annual.period_end, date(2023, 9, 30))
        self.assertAlmostEqual(annual.value, 383285000000)
        self.assertEqual(annual.source, "edgar")
        self.assertEqual(annual.currency, "USD")
        self.assertEqual(annual.as_of_ts.date(), date(2023, 11, 3))

    def test_404_tries_next_raw_tag(self):
        # Sequence: first tag 404, second tag 200 with one row.
        seq = [_edgar_response({}, status=404), _edgar_response({
            "units": {"USD": [{"end": "2024-03-31", "val": 1000, "fp": "Q1", "form": "10-Q", "filed": "2024-05-01"}]}
        })]
        def _fake_get(url, headers):
            return seq.pop(0) if seq else _edgar_response({}, status=404)

        facts = fetch_us_financials(
            cik="320193", ticker="AAPL",
            concepts=["REVENUE"],
            http_get=_fake_get,
        )
        # Should find on second tag (RevenueFromContract... or SalesRevenueNet)
        self.assertEqual(len(facts), 1)

    def test_missing_user_agent_returns_empty(self):
        os.environ.pop("SEC_USER_AGENT", None)
        reset_settings_cache()
        try:
            facts = fetch_us_financials(
                cik="320193", ticker="AAPL",
                concepts=["REVENUE"],
                http_get=lambda u, h: _edgar_response({"units": {"USD": []}}),
            )
            self.assertEqual(facts, [])
        finally:
            os.environ["SEC_USER_AGENT"] = "test-bot test@example.com"
            reset_settings_cache()

    def test_skips_unusual_form_like_8k(self):
        payload = {
            "units": {
                "USD": [
                    {"end": "2024-03-31", "val": 1, "form": "8-K", "filed": "2024-04-01"},
                    {"end": "2024-03-31", "val": 2, "fp": "Q1", "form": "10-Q", "filed": "2024-05-01"},
                ]
            }
        }
        facts = fetch_us_financials(
            cik="X", ticker="AAPL", concepts=["REVENUE"],
            http_get=lambda u, h: _edgar_response(payload),
        )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].value, 2)


# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class SyncFinancialsLoaderTest(unittest.TestCase):
    def _session(self):
        s = MagicMock()
        s.scalars.return_value.first.return_value = None
        s.execute.return_value.__iter__ = lambda self_: iter([])
        return s

    def test_processes_each_ticker(self):
        def _fetcher(ticker):
            return [FinancialFactRow(
                market=Market.US, ticker=ticker, concept="REVENUE",
                period_end=date(2024, 3, 31), period_kind="Q",
                value=1000.0, currency="USD", source="mock",
                as_of_ts=datetime.now(UTC),
            )]

        report = sync_financials(
            self._session(), market=Market.US,
            tickers=["AAPL", "MSFT"], fetcher=_fetcher,
        )
        self.assertEqual(report.tickers_processed, 2)
        self.assertEqual(report.rows_upserted, 2)
        self.assertEqual(report.tickers_failed, 0)

    def test_failure_does_not_stop_batch(self):
        def _fetcher(ticker):
            if ticker == "BAD":
                raise RuntimeError("nope")
            return [FinancialFactRow(
                market=Market.US, ticker=ticker, concept="NET_INCOME",
                period_end=date(2024, 3, 31), period_kind="Q",
                value=100.0, currency="USD", source="mock",
                as_of_ts=datetime.now(UTC),
            )]

        report = sync_financials(
            self._session(), market=Market.US,
            tickers=["AAPL", "BAD", "MSFT"], fetcher=_fetcher,
        )
        self.assertEqual(report.tickers_processed, 2)
        self.assertEqual(report.tickers_failed, 1)

    def test_no_fetcher_returns_error_report(self):
        report = sync_financials(
            self._session(), market=Market.US,
            tickers=["AAPL"], fetcher=None,
        )
        self.assertEqual(report.tickers_processed, 0)
        self.assertTrue(report.errors)


if __name__ == "__main__":
    unittest.main()
