"""Price adapter + loader tests with mocked external sources."""
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
    from data.price.kr_prices import fetch_kr_daily  # noqa: E402
    from data.price.us_prices import fetch_us_daily  # noqa: E402
    from data.price.loader import sync_daily_prices  # noqa: E402
    from data.price.types import DailyBar  # noqa: E402


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class KrPriceAdapterTest(unittest.TestCase):
    def test_basic_parse_with_korean_columns(self):
        rows = [
            {"날짜": "2024-01-02", "시가": 70000, "고가": 70500, "저가": 69500, "종가": 70200, "거래량": 1000000},
            {"날짜": "2024-01-03", "시가": 70200, "고가": 71000, "저가": 70100, "종가": 70800, "거래량": 1200000},
        ]
        bars = fetch_kr_daily(
            ticker="005930",
            start=date(2024, 1, 1),
            end=date(2024, 1, 5),
            fetch_ohlcv=lambda a, b, c: rows,
        )
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].open, 70000)
        self.assertEqual(bars[0].close, 70200)
        self.assertEqual(bars[0].market, Market.KR)
        self.assertEqual(bars[0].source, "pykrx")
        self.assertIsNotNone(bars[0].as_of_ts)
        # KR close 15:30 KST → 06:32 UTC + 2min lag
        self.assertEqual(bars[0].as_of_ts.tzinfo.tzname(bars[0].as_of_ts), "UTC")

    def test_basic_parse_with_english_columns(self):
        rows = [
            {"date": "2024-01-02", "Open": 100, "High": 105, "Low": 99, "Close": 104, "Volume": 5000},
        ]
        bars = fetch_kr_daily(
            ticker="000660",
            start=date(2024, 1, 1),
            end=date(2024, 1, 5),
            fetch_ohlcv=lambda a, b, c: rows,
        )
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 104)

    def test_ticker_is_normalised(self):
        bars = fetch_kr_daily(
            ticker="5930",  # short form
            start=date(2024, 1, 1), end=date(2024, 1, 2),
            fetch_ohlcv=lambda a, b, c: [{"date": "2024-01-02", "Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1}],
        )
        self.assertEqual(bars[0].ticker, "005930")

    def test_fetcher_exception_returns_empty(self):
        bars = fetch_kr_daily(
            ticker="005930",
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            fetch_ohlcv=lambda a, b, c: (_ for _ in ()).throw(RuntimeError("KRX down")),
        )
        self.assertEqual(bars, [])

    def test_investor_columns_attached_when_date_matches(self):
        ohlcv = [{"date": "2024-01-02", "Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1}]
        investor = [{"date": "2024-01-02", "외국인합계": 500_000, "기관합계": -200_000}]
        bars = fetch_kr_daily(
            ticker="005930",
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            fetch_ohlcv=lambda *a: ohlcv,
            fetch_investor=lambda *a: investor,
        )
        self.assertEqual(bars[0].foreign_net, 500_000)
        self.assertEqual(bars[0].institution_net, -200_000)


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class UsPriceAdapterTest(unittest.TestCase):
    def test_basic_parse(self):
        rows = [
            {"Date": "2024-01-02", "Open": 190.0, "High": 192.0, "Low": 189.5,
             "Close": 191.0, "Adj Close": 190.8, "Volume": 50_000_000},
        ]
        bars = fetch_us_daily(
            ticker="AAPL",
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            fetch_history=lambda t, s, e: rows,
        )
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 191.0)
        self.assertEqual(bars[0].adj_close, 190.8)
        self.assertEqual(bars[0].market, Market.US)
        self.assertEqual(bars[0].source, "yfinance")
        self.assertIsNotNone(bars[0].as_of_ts)

    def test_class_share_ticker_converted_for_yfinance_call(self):
        captured: dict = {}

        def _fake(ticker, start, end):
            captured["ticker"] = ticker
            return []

        fetch_us_daily(
            ticker="BRK.B",
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            fetch_history=_fake,
        )
        # yfinance wants 'BRK-B'
        self.assertEqual(captured["ticker"], "BRK-B")

    def test_fetcher_exception_returns_empty(self):
        bars = fetch_us_daily(
            ticker="AAPL",
            start=date(2024, 1, 1), end=date(2024, 1, 5),
            fetch_history=lambda *a: (_ for _ in ()).throw(RuntimeError("yf down")),
        )
        self.assertEqual(bars, [])


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class SyncDailyPricesTest(unittest.TestCase):
    """Loader orchestration. Uses mocked session — DB integration
    lives in tests/integration/test_price_db.py (deferred)."""

    def _make_session(self) -> MagicMock:
        session = MagicMock()
        session.scalars.return_value.first.return_value = None
        session.execute.return_value.__iter__ = lambda self_: iter([])
        return session

    def test_reports_per_ticker_processing(self):
        session = self._make_session()

        def fake_fetcher(ticker, start, end):
            return [DailyBar(
                market=Market.KR, ticker=ticker, trade_date=start,
                open=1, high=1, low=1, close=1, volume=1,
                source="mock", as_of_ts=datetime.now(UTC),
            )]

        report = sync_daily_prices(
            session, market=Market.KR,
            start=date(2024, 1, 2), end=date(2024, 1, 2),
            tickers=["005930", "000660", "066570"],
            fetcher=fake_fetcher,
        )
        self.assertEqual(report.tickers_processed, 3)
        self.assertEqual(report.tickers_failed, 0)
        self.assertEqual(report.rows_upserted, 3)

    def test_individual_failures_counted_not_fatal(self):
        session = self._make_session()

        def fake_fetcher(ticker, start, end):
            if ticker == "BADBADBAD":
                raise RuntimeError("nope")
            return [DailyBar(
                market=Market.US, ticker=ticker, trade_date=start,
                open=1, high=1, low=1, close=1, volume=1, source="mock",
                as_of_ts=datetime.now(UTC),
            )]

        report = sync_daily_prices(
            session, market=Market.US,
            start=date(2024, 1, 2), end=date(2024, 1, 2),
            tickers=["AAPL", "BADBADBAD", "MSFT"],
            fetcher=fake_fetcher,
        )
        self.assertEqual(report.tickers_processed, 2)
        self.assertEqual(report.tickers_failed, 1)
        self.assertEqual(report.rows_upserted, 2)
        self.assertEqual(len(report.errors), 1)

    def test_no_tickers_returns_error_report(self):
        session = self._make_session()
        report = sync_daily_prices(
            session, market=Market.KR,
            start=date(2024, 1, 1), end=date(2024, 1, 1),
            tickers=[], fetcher=lambda *a: [],
        )
        self.assertEqual(report.tickers_processed, 0)
        self.assertIn("no active tickers", report.errors)


if __name__ == "__main__":
    unittest.main()
