"""KR market adapter tests — calendar / tax / ticker.

Calendar tests use only known-stable holidays so they don't break
when exchange_calendars updates its holiday tables for newer years.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from markets.kr.ticker import is_valid_kr_ticker, normalize_kr_ticker  # noqa: E402
from markets.kr.tax import (  # noqa: E402
    DEFAULT_COMMISSION_BPS,
    SELL_TX_TAX_BPS,
    CLEARING_FEE_BPS,
    compute_kr_tax,
)


class KrTickerTest(unittest.TestCase):
    def test_six_digit_passes_through(self):
        self.assertEqual(normalize_kr_ticker("005930"), "005930")

    def test_short_numeric_is_zero_padded(self):
        self.assertEqual(normalize_kr_ticker("5930"), "005930")
        self.assertEqual(normalize_kr_ticker("930"), "000930")

    def test_yahoo_suffix_is_stripped(self):
        self.assertEqual(normalize_kr_ticker("005930.KS"), "005930")
        self.assertEqual(normalize_kr_ticker("066570.kq"), "066570")
        self.assertEqual(normalize_kr_ticker("000660.KOSPI"), "000660")

    def test_a_prefix_is_stripped(self):
        self.assertEqual(normalize_kr_ticker("A005930"), "005930")

    def test_whitespace_is_stripped(self):
        self.assertEqual(normalize_kr_ticker("  005930  "), "005930")

    def test_invalid_inputs_raise(self):
        for bad in ("", " ", "ABCDEF", "1234567", "00593X", None):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                normalize_kr_ticker(bad)  # type: ignore[arg-type]

    def test_is_valid_does_not_raise(self):
        self.assertTrue(is_valid_kr_ticker("005930"))
        self.assertTrue(is_valid_kr_ticker("5930"))
        self.assertFalse(is_valid_kr_ticker("AAPL"))
        self.assertFalse(is_valid_kr_ticker(""))


class KrTaxTest(unittest.TestCase):
    def test_buy_has_no_transaction_tax(self):
        result = compute_kr_tax(side="BUY", gross_value=10_000_000)
        self.assertEqual(result.transaction_tax, 0.0)
        # commission default 1.5 bps + clearing 0.36 bps
        expected_comm = 10_000_000 * DEFAULT_COMMISSION_BPS / 10_000
        expected_clear = 10_000_000 * CLEARING_FEE_BPS / 10_000
        self.assertAlmostEqual(result.commission, round(expected_comm, 4))
        self.assertAlmostEqual(result.other_fees, round(expected_clear, 4))

    def test_sell_includes_transaction_tax(self):
        result = compute_kr_tax(side="SELL", gross_value=10_000_000)
        expected_tax = 10_000_000 * SELL_TX_TAX_BPS / 10_000  # 0.15 %
        self.assertAlmostEqual(result.transaction_tax, round(expected_tax, 4))

    def test_total_sums_components(self):
        result = compute_kr_tax(side="SELL", gross_value=5_000_000)
        self.assertAlmostEqual(
            result.total, result.commission + result.transaction_tax + result.other_fees
        )

    def test_custom_commission(self):
        result = compute_kr_tax(side="BUY", gross_value=1_000_000, commission_bps=5.0)
        self.assertAlmostEqual(result.commission, 500.0)  # 1,000,000 * 5 / 10,000

    def test_zero_value(self):
        result = compute_kr_tax(side="BUY", gross_value=0)
        self.assertEqual(result.total, 0.0)

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            compute_kr_tax(side="BUY", gross_value=-1)

    def test_bad_side_raises(self):
        with self.assertRaises(ValueError):
            compute_kr_tax(side="HOLD", gross_value=1000)


@unittest.skipUnless(
    __import__("importlib").util.find_spec("exchange_calendars"),
    "exchange_calendars not installed (run `uv sync` first)",
)
class KrCalendarTest(unittest.TestCase):
    """Skipped automatically when the optional exchange_calendars dep
    is not installed yet (it joins the env at `uv sync` time)."""

    def test_known_trading_day(self):
        from markets.kr.calendar import is_trading_day

        # 2024-01-02 was the first KRX trading day of 2024.
        self.assertTrue(is_trading_day(date(2024, 1, 2)))

    def test_known_holiday(self):
        from markets.kr.calendar import is_trading_day

        # 2024-01-01 New Year's Day — never a trading day.
        self.assertFalse(is_trading_day(date(2024, 1, 1)))

    def test_weekend_is_not_trading_day(self):
        from markets.kr.calendar import is_trading_day

        # 2024-01-06 was a Saturday.
        self.assertFalse(is_trading_day(date(2024, 1, 6)))

    def test_previous_and_next_trading_day(self):
        from markets.kr.calendar import next_trading_day, previous_trading_day

        # Around 2024 New Year's holiday.
        self.assertEqual(next_trading_day(date(2023, 12, 29)), date(2024, 1, 2))
        self.assertEqual(previous_trading_day(date(2024, 1, 2)), date(2023, 12, 28))


if __name__ == "__main__":
    unittest.main()
