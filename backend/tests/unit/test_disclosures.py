"""Disclosure adapter + loader tests with mocks."""
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
    from data.disclosures.kr_dart import fetch_kr_disclosures  # noqa: E402
    from data.disclosures.loader import sync_disclosures  # noqa: E402
    from data.disclosures.types import (  # noqa: E402
        ANNUAL,
        INSIDER,
        MATERIAL_EVENT,
        OTHER,
        QUARTERLY,
        DisclosureRow,
    )
    from data.disclosures.us_edgar import fetch_us_disclosures  # noqa: E402


def _edgar_response(payload: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = lambda: None if status < 400 else (_ for _ in ()).throw(RuntimeError("status"))
    resp.json.return_value = payload
    return resp


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class KrDisclosuresAdapterTest(unittest.TestCase):
    def test_basic_annual_filing(self):
        rows = [
            {"rcept_no": "20240314000001", "report_nm": "사업보고서 (2023.12)",
             "rcept_dt": "20240314"},
            {"rcept_no": "20240515000002", "report_nm": "분기보고서 (2024.03)",
             "rcept_dt": "20240515"},
            {"rcept_no": "20240601000003", "report_nm": "주요사항보고서(자기주식취득)",
             "rcept_dt": "20240601"},
            {"rcept_no": "20240701000004", "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서",
             "rcept_dt": "20240701"},
            {"rcept_no": "", "report_nm": "garbage"},  # skipped
        ]
        items = fetch_kr_disclosures(
            corp_code="00126380", ticker="005930",
            start=date(2024, 1, 1), end=date(2024, 12, 31),
            fetch_list=lambda *a, **k: rows,
        )
        by_id = {r.source_id: r for r in items}
        self.assertEqual(len(items), 4)
        self.assertEqual(by_id["20240314000001"].filing_type_canonical, ANNUAL)
        self.assertEqual(by_id["20240515000002"].filing_type_canonical, QUARTERLY)
        self.assertEqual(by_id["20240601000003"].filing_type_canonical, MATERIAL_EVENT)
        self.assertEqual(by_id["20240701000004"].filing_type_canonical, INSIDER)
        for r in items:
            self.assertEqual(r.market, Market.KR)
            self.assertEqual(r.source, "dart")
            self.assertIsNotNone(r.source_url)
            self.assertIsNotNone(r.filing_ts)
            self.assertIsNotNone(r.as_of_ts)

    def test_skips_invalid_date(self):
        rows = [{"rcept_no": "X", "report_nm": "사업보고서", "rcept_dt": "garbage"}]
        items = fetch_kr_disclosures(
            corp_code="X", ticker="005930",
            start=date(2024, 1, 1), end=date(2024, 12, 31),
            fetch_list=lambda *a, **k: rows,
        )
        self.assertEqual(items, [])

    def test_fetch_exception_returns_empty(self):
        def _boom(*a, **k):
            raise RuntimeError("DART down")

        items = fetch_kr_disclosures(
            corp_code="X", ticker="005930",
            start=date(2024, 1, 1), end=date(2024, 12, 31),
            fetch_list=_boom,
        )
        self.assertEqual(items, [])


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class UsDisclosuresAdapterTest(unittest.TestCase):
    def test_basic_canonical_filter(self):
        payload = {
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-24-000001", "0000320193-24-000002", "0000320193-24-000003"],
                    "form": ["10-K", "8-K", "10-Q"],
                    "filingDate": ["2024-11-01", "2024-09-15", "2024-08-02"],
                    "primaryDocument": ["aapl-10k.htm", "aapl-8k.htm", "aapl-10q.htm"],
                    "primaryDocDescription": [
                        "Annual Report", "Material Event", "Quarterly Report",
                    ],
                }
            }
        }
        items = fetch_us_disclosures(
            cik="320193", ticker="AAPL",
            http_get=lambda u, h: _edgar_response(payload),
        )
        kinds = [r.filing_type_canonical for r in items]
        self.assertIn(ANNUAL, kinds)
        self.assertIn(QUARTERLY, kinds)
        self.assertIn(MATERIAL_EVENT, kinds)
        for r in items:
            self.assertEqual(r.market, Market.US)
            self.assertEqual(r.source, "edgar")
            self.assertTrue(r.source_url.startswith("https://www.sec.gov/"))

    def test_skips_non_canonical_forms_by_default(self):
        payload = {
            "filings": {
                "recent": {
                    "accessionNumber": ["x", "y"],
                    "form": ["10-K", "S-1"],  # S-1 is not canonical
                    "filingDate": ["2024-01-01", "2024-02-01"],
                    "primaryDocument": ["a.htm", "b.htm"],
                    "primaryDocDescription": ["10K", "S1"],
                }
            }
        }
        items = fetch_us_disclosures(
            cik="X", ticker="AAPL",
            http_get=lambda u, h: _edgar_response(payload),
        )
        forms = [r.filing_type for r in items]
        self.assertEqual(forms, ["10-K"])

    def test_only_forms_filter(self):
        payload = {
            "filings": {
                "recent": {
                    "accessionNumber": ["x", "y", "z"],
                    "form": ["10-K", "8-K", "10-Q"],
                    "filingDate": ["2024-01-01", "2024-02-01", "2024-03-01"],
                    "primaryDocument": ["a", "b", "c"],
                    "primaryDocDescription": ["", "", ""],
                }
            }
        }
        items = fetch_us_disclosures(
            cik="X", ticker="AAPL",
            http_get=lambda u, h: _edgar_response(payload),
            only_forms={"8-K"},
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].filing_type, "8-K")
        self.assertEqual(items[0].filing_type_canonical, MATERIAL_EVENT)

    def test_missing_user_agent_returns_empty(self):
        os.environ.pop("SEC_USER_AGENT", None)
        reset_settings_cache()
        try:
            items = fetch_us_disclosures(
                cik="X", ticker="AAPL",
                http_get=lambda u, h: _edgar_response({"filings": {"recent": {}}}),
            )
            self.assertEqual(items, [])
        finally:
            os.environ["SEC_USER_AGENT"] = "test-bot test@example.com"
            reset_settings_cache()

    def test_insider_form_recognised(self):
        payload = {
            "filings": {
                "recent": {
                    "accessionNumber": ["x"],
                    "form": ["4"],
                    "filingDate": ["2024-01-01"],
                    "primaryDocument": ["form4.xml"],
                    "primaryDocDescription": ["Insider transaction"],
                }
            }
        }
        items = fetch_us_disclosures(
            cik="X", ticker="AAPL",
            http_get=lambda u, h: _edgar_response(payload),
        )
        self.assertEqual(items[0].filing_type_canonical, INSIDER)


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class SyncDisclosuresLoaderTest(unittest.TestCase):
    def _session(self):
        s = MagicMock()
        s.scalars.return_value.first.return_value = None
        s.execute.return_value.__iter__ = lambda self_: iter([])
        # session.execute(insert_stmt).rowcount — set positive count
        s.execute.return_value.rowcount = 1
        return s

    def test_processes_each_ticker(self):
        def _fetcher(ticker):
            return [DisclosureRow(
                market=Market.KR, ticker=ticker,
                source="dart", source_id=f"id-{ticker}",
                filing_date=date(2024, 5, 1), filing_type="분기보고서",
                filing_type_canonical=QUARTERLY,
                title="Q",
                source_url=None, filing_ts=None,
                as_of_ts=datetime.now(UTC),
            )]
        report = sync_disclosures(
            self._session(), market=Market.KR,
            tickers=["005930", "000660"], fetcher=_fetcher,
        )
        self.assertEqual(report.tickers_processed, 2)
        self.assertEqual(report.tickers_failed, 0)

    def test_per_ticker_failure_continues(self):
        def _fetcher(ticker):
            if ticker == "BAD":
                raise RuntimeError("nope")
            return []
        report = sync_disclosures(
            self._session(), market=Market.KR,
            tickers=["A", "BAD", "B"], fetcher=_fetcher,
        )
        self.assertEqual(report.tickers_processed, 2)
        self.assertEqual(report.tickers_failed, 1)

    def test_no_fetcher_returns_error(self):
        report = sync_disclosures(
            self._session(), market=Market.KR,
            tickers=["A"], fetcher=None,
        )
        self.assertTrue(report.errors)


if __name__ == "__main__":
    unittest.main()
