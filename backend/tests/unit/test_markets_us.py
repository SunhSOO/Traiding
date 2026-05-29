"""US market adapter tests — calendar / tax / ticker."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from markets.us.ticker import is_valid_us_ticker, normalize_us_ticker  # noqa: E402
from markets.us.tax import (  # noqa: E402
    FINRA_TAF_CAP,
    FINRA_TAF_PER_SHARE,
    SEC_FEE_PER_USD,
    compute_us_tax,
)


class UsTickerTest(unittest.TestCase):
    def test_basic_passthrough(self):
        self.assertEqual(normalize_us_ticker("AAPL"), "AAPL")
        self.assertEqual(normalize_us_ticker("msft"), "MSFT")

    def test_strip_yahoo_suffix(self):
        self.assertEqual(normalize_us_ticker("AAPL.OQ"), "AAPL")
        self.assertEqual(normalize_us_ticker("BAC.N"), "BAC")

    def test_class_share_normalization(self):
        self.assertEqual(normalize_us_ticker("BRK-B"), "BRK.B")
        self.assertEqual(normalize_us_ticker("BRK/B"), "BRK.B")
        self.assertEqual(normalize_us_ticker("brk.b"), "BRK.B")

    def test_invalid_inputs_raise(self):
        for bad in ("", " ", "12345", "TOOLONG7", None):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                normalize_us_ticker(bad)  # type: ignore[arg-type]

    def test_is_valid(self):
        self.assertTrue(is_valid_us_ticker("AAPL"))
        self.assertTrue(is_valid_us_ticker("BRK.B"))
        self.assertFalse(is_valid_us_ticker("005930"))
        self.assertFalse(is_valid_us_ticker(""))


class UsTaxTest(unittest.TestCase):
    def test_buy_has_no_tax(self):
        result = compute_us_tax(side="BUY", gross_value=100_000)
        self.assertEqual(result.transaction_tax, 0.0)
        self.assertEqual(result.commission, 0.0)  # default $0 commission
        self.assertEqual(result.other_fees, 0.0)

    def test_sell_has_sec_fee(self):
        result = compute_us_tax(side="SELL", gross_value=100_000)
        expected_sec = 100_000 * SEC_FEE_PER_USD
        # No shares passed → no FINRA TAF
        self.assertAlmostEqual(result.transaction_tax, round(expected_sec, 4))

    def test_sell_with_shares_has_taf(self):
        result = compute_us_tax(side="SELL", gross_value=100_000, shares=1000)
        expected_sec = 100_000 * SEC_FEE_PER_USD
        expected_taf = 1000 * FINRA_TAF_PER_SHARE  # 1000 * 0.000166 = 0.166
        self.assertAlmostEqual(
            result.transaction_tax, round(expected_sec + expected_taf, 4), places=4
        )

    def test_taf_is_capped(self):
        # Huge share count → TAF should hit the cap
        result = compute_us_tax(side="SELL", gross_value=100_000_000, shares=1_000_000)
        # TAF would be 1,000,000 * 0.000166 = 166, capped at 8.30
        # So transaction_tax = SEC_fee + 8.30
        expected_sec = 100_000_000 * SEC_FEE_PER_USD
        self.assertAlmostEqual(
            result.transaction_tax,
            round(expected_sec + FINRA_TAF_CAP, 4),
            places=2,
        )

    def test_commission_bps_override(self):
        result = compute_us_tax(side="BUY", gross_value=10_000, commission_bps=5)
        self.assertAlmostEqual(result.commission, 5.0)  # 10,000 * 5 / 10_000

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            compute_us_tax(side="BUY", gross_value=-1)


@unittest.skipUnless(
    __import__("importlib").util.find_spec("exchange_calendars"),
    "exchange_calendars not installed (run `uv sync` first)",
)
class UsCalendarTest(unittest.TestCase):
    def test_known_trading_day(self):
        from markets.us.calendar import is_trading_day

        # 2024-01-02 was the first NYSE trading day of 2024.
        self.assertTrue(is_trading_day(date(2024, 1, 2)))

    def test_known_holiday(self):
        from markets.us.calendar import is_trading_day

        # 2024-07-04 Independence Day.
        self.assertFalse(is_trading_day(date(2024, 7, 4)))

    def test_thanksgiving_thursday(self):
        from markets.us.calendar import is_trading_day

        # 2024-11-28 Thanksgiving Thursday.
        self.assertFalse(is_trading_day(date(2024, 11, 28)))


class UsAdapterRegistryTest(unittest.TestCase):
    def test_registry_returns_correct_adapter(self):
        from core.types import Market
        from markets import get_adapter

        kr = get_adapter(Market.KR)
        us = get_adapter(Market.US)
        self.assertEqual(kr.base_currency, "KRW")
        self.assertEqual(us.base_currency, "USD")

    def test_registry_accepts_string(self):
        from markets import get_adapter

        self.assertEqual(get_adapter("KR").base_currency, "KRW")
        self.assertEqual(get_adapter("us").base_currency, "USD")

    def test_unknown_market_raises(self):
        from markets import get_adapter

        with self.assertRaises(ValueError):
            get_adapter("XX")


if __name__ == "__main__":
    unittest.main()
