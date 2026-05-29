"""Universe loader tests.

Pure-fetcher tests use mocked pykrx/FDR callables (no network).
Loader (DB-side) tests use mocked SQLAlchemy session — DB
integration deferred until alembic upgrade head runs.
"""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import sqlalchemy  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from core.types import Market  # noqa: E402
    from data.universe.types import SecurityInfo  # noqa: E402
    from data.universe.kr_universe import fetch_kr_universe  # noqa: E402
    from data.universe.us_universe import fetch_us_universe  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Pure fetcher tests (no DB)
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class KrUniverseFetcherTest(unittest.TestCase):
    def test_returns_normalized_tickers(self):
        index_data = {
            "1028": ["005930", "000660"],     # KOSPI 200
            "2203": ["066570"],                # KOSDAQ 150
        }
        names = {"005930": "삼성전자", "000660": "SK하이닉스", "066570": "LG전자"}
        markets = {"005930": "KOSPI", "000660": "KOSPI", "066570": "KOSDAQ"}

        infos = fetch_kr_universe(
            date(2026, 5, 26),
            fetch_index_portfolio=lambda d, k: index_data.get(k, []),
            fetch_market_tickers=lambda d, m: [],
            fetch_ticker_name=lambda t: names.get(t, t),
            fetch_market_for_ticker=lambda t: markets.get(t, "KOSPI"),
        )
        by_ticker = {i.ticker: i for i in infos}
        self.assertIn("005930", by_ticker)
        self.assertEqual(by_ticker["005930"].name, "삼성전자")
        self.assertEqual(by_ticker["005930"].currency, "KRW")
        self.assertIn("KOSPI200", by_ticker["005930"].index_codes)
        self.assertIn("KOSDAQ150", by_ticker["066570"].index_codes)

    def test_ticker_in_both_indices(self):
        index_data = {
            "1028": ["005930"],
            "2203": ["005930"],  # also in KOSDAQ150 (hypothetical)
        }
        infos = fetch_kr_universe(
            date(2026, 5, 26),
            fetch_index_portfolio=lambda d, k: index_data.get(k, []),
            fetch_market_tickers=lambda d, m: [],
            fetch_ticker_name=lambda t: "X",
            fetch_market_for_ticker=lambda t: "KOSPI",
        )
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].index_codes, frozenset({"KOSPI200", "KOSDAQ150"}))

    def test_resilient_to_fetcher_errors(self):
        def _boom(*args, **kwargs):
            raise RuntimeError("KRX unreachable")

        infos = fetch_kr_universe(
            date(2026, 5, 26),
            fetch_index_portfolio=_boom,
            fetch_market_tickers=lambda d, m: [],
            fetch_ticker_name=lambda t: t,
            fetch_market_for_ticker=lambda t: "KOSPI",
        )
        # Both indices fail → no rows, no exception bubbled up
        self.assertEqual(infos, [])

    def test_resilient_to_unparseable_ticker(self):
        # normalize_kr_ticker raises on truly bad input but pykrx never
        # returns those in practice; this just confirms the loader
        # passes through valid forms unchanged.
        infos = fetch_kr_universe(
            date(2026, 5, 26),
            fetch_index_portfolio=lambda d, k: ["005930"],
            fetch_market_tickers=lambda d, m: [],
            fetch_ticker_name=lambda t: "삼성전자",
            fetch_market_for_ticker=lambda t: "KOSPI",
        )
        self.assertEqual(infos[0].ticker, "005930")


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class UsUniverseFetcherTest(unittest.TestCase):
    def test_basic_listing(self):
        listings = {
            "S&P500": [
                {"Symbol": "AAPL", "Name": "Apple Inc.", "Sector": "Technology"},
                {"Symbol": "BRK-B", "Name": "Berkshire Hathaway"},
            ],
            "NASDAQ100": [
                {"Symbol": "AAPL", "Name": "Apple Inc."},
                {"Symbol": "NVDA", "Name": "NVIDIA Corp."},
            ],
        }
        infos = fetch_us_universe(
            date(2026, 5, 26),
            fetch_listing=lambda code: listings.get(code, []),
        )
        by_ticker = {i.ticker: i for i in infos}
        self.assertIn("AAPL", by_ticker)
        self.assertEqual(by_ticker["AAPL"].index_codes, frozenset({"SP500", "NASDAQ100"}))
        # BRK-B should be normalised to BRK.B
        self.assertIn("BRK.B", by_ticker)
        self.assertEqual(by_ticker["BRK.B"].name, "Berkshire Hathaway")
        # NVDA only in NASDAQ100
        self.assertEqual(by_ticker["NVDA"].index_codes, frozenset({"NASDAQ100"}))
        # All USD
        for info in infos:
            self.assertEqual(info.currency, "USD")

    def test_skips_unparseable_symbol(self):
        listings = {
            "S&P500": [
                {"Symbol": "AAPL", "Name": "Apple"},
                {"Symbol": "12345", "Name": "Bad"},  # not a valid US ticker
                {"Symbol": None, "Name": "Missing"},
            ],
            "NASDAQ100": [],
        }
        infos = fetch_us_universe(
            date(2026, 5, 26),
            fetch_listing=lambda code: listings.get(code, []),
        )
        tickers = {i.ticker for i in infos}
        self.assertEqual(tickers, {"AAPL"})

    def test_accepts_lower_case_keys(self):
        # Wikipedia/FDR can return either capitalization
        listings = {
            "S&P500": [{"symbol": "TSLA", "name": "Tesla"}],
            "NASDAQ100": [],
        }
        infos = fetch_us_universe(
            date(2026, 5, 26),
            fetch_listing=lambda code: listings.get(code, []),
        )
        self.assertEqual(infos[0].ticker, "TSLA")


# ──────────────────────────────────────────────────────────────────────
# SecurityInfo merging
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class SecurityInfoMergeTest(unittest.TestCase):
    def test_merge_picks_non_null(self):
        a = SecurityInfo(market=Market.US, ticker="AAPL", name="Apple", sector=None, currency="USD")
        b = SecurityInfo(market=Market.US, ticker="AAPL", name="Apple Inc.", sector="Tech", currency="USD",
                          index_codes=frozenset({"SP500"}))
        merged = a.merged_with(b)
        self.assertEqual(merged.sector, "Tech")
        # `self`'s non-None name wins (Apple, not Apple Inc.)
        self.assertEqual(merged.name, "Apple")
        self.assertEqual(merged.index_codes, frozenset({"SP500"}))

    def test_merge_unions_index_codes(self):
        a = SecurityInfo(market=Market.US, ticker="AAPL", name="Apple", currency="USD",
                         index_codes=frozenset({"SP500"}))
        b = SecurityInfo(market=Market.US, ticker="AAPL", name="Apple", currency="USD",
                         index_codes=frozenset({"NASDAQ100"}))
        merged = a.merged_with(b)
        self.assertEqual(merged.index_codes, frozenset({"SP500", "NASDAQ100"}))

    def test_merge_refuses_cross_key(self):
        a = SecurityInfo(market=Market.US, ticker="AAPL", name="x", currency="USD")
        b = SecurityInfo(market=Market.US, ticker="MSFT", name="x", currency="USD")
        with self.assertRaises(ValueError):
            a.merged_with(b)


# ──────────────────────────────────────────────────────────────────────
# Loader (DB-side) — uses a fake session that mimics SQLAlchemy enough
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class LoaderReportsTest(unittest.TestCase):
    """Just exercises that the orchestrator runs both markets, swallows
    fetcher errors into the SyncReport, and stamps freshness. We don't
    assert exact INSERT/UPDATE row deltas here (that requires the real
    DB); those come in tests/integration/test_universe_db.py."""

    def test_orchestrator_runs_both_markets(self):
        from data.universe.loader import sync_universe

        session = MagicMock()
        # session.scalars(...).first() returns None (no existing rows)
        session.scalars.return_value.first.return_value = None
        # session.execute(select(...)) returns empty (no existing keys, no open memberships)
        session.execute.return_value.__iter__ = lambda self_: iter([])
        # session.scalars(select(UniverseMembership)) iterates empty
        session.scalars.return_value.__iter__ = lambda self_: iter([])

        kr_infos = [SecurityInfo(market=Market.KR, ticker="005930", name="x", currency="KRW",
                                 index_codes=frozenset({"KOSPI200"}))]
        us_infos = [SecurityInfo(market=Market.US, ticker="AAPL", name="Apple", currency="USD",
                                 index_codes=frozenset({"SP500"}))]

        reports = sync_universe(
            session,
            as_of_date=date(2026, 5, 26),
            kr_fetchers={
                "fetch_index_portfolio": lambda d, k: ["005930"] if k == "1028" else [],
                "fetch_market_tickers": lambda d, m: [],
                "fetch_ticker_name": lambda t: "삼성전자",
                "fetch_market_for_ticker": lambda t: "KOSPI",
            },
            us_fetchers={
                "fetch_listing": lambda code: (
                    [{"Symbol": "AAPL", "Name": "Apple"}] if code == "S&P500" else []
                ),
            },
        )
        self.assertEqual(len(reports), 2)
        markets = {r.market for r in reports}
        self.assertEqual(markets, {Market.KR, Market.US})
        for r in reports:
            self.assertGreater(r.fetched_count, 0, msg=r.error)
            self.assertIsNone(r.error)

    def test_skip_market_when_fetchers_none(self):
        from data.universe.loader import sync_universe

        session = MagicMock()
        session.scalars.return_value.first.return_value = None
        session.execute.return_value.__iter__ = lambda self_: iter([])
        session.scalars.return_value.__iter__ = lambda self_: iter([])

        reports = sync_universe(
            session,
            as_of_date=date(2026, 5, 26),
            kr_fetchers=None,
            us_fetchers={"fetch_listing": lambda code: [{"Symbol": "AAPL", "Name": "Apple"}]},
        )
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].market, Market.US)

    def test_fetch_failure_becomes_error_report(self):
        from data.universe.loader import sync_universe

        session = MagicMock()
        session.scalars.return_value.first.return_value = None
        session.execute.return_value.__iter__ = lambda self_: iter([])
        session.scalars.return_value.__iter__ = lambda self_: iter([])

        def _boom(code):
            raise RuntimeError("API down")

        reports = sync_universe(
            session,
            as_of_date=date(2026, 5, 26),
            kr_fetchers=None,
            us_fetchers={"fetch_listing": _boom},
        )
        # Note: fetch_us_universe wraps individual index fetches in try/except,
        # so _boom there returns []. The orchestrator then reports
        # "fetch returned zero rows" rather than the inner exception.
        self.assertEqual(len(reports), 1)
        self.assertIsNotNone(reports[0].error)


if __name__ == "__main__":
    unittest.main()
