"""US market adapter — NYSE + NASDAQ."""
from __future__ import annotations

from datetime import date as DateType, datetime

from core.types import Market
from markets.base import MarketAdapter, TaxBreakdown
from markets.us.calendar import is_trading_day, next_trading_day, previous_trading_day, session_open_close_utc
from markets.us.tax import compute_us_tax
from markets.us.ticker import is_valid_us_ticker, normalize_us_ticker


class USMarketAdapter(MarketAdapter):
    market = Market.US

    @property
    def base_currency(self) -> str:
        return "USD"

    @property
    def display_name(self) -> str:
        return "NYSE + NASDAQ"

    def is_trading_day(self, d: DateType) -> bool:
        return is_trading_day(d)

    def previous_trading_day(self, d: DateType) -> DateType:
        return previous_trading_day(d)

    def next_trading_day(self, d: DateType) -> DateType:
        return next_trading_day(d)

    def session_open_close_utc(self, d: DateType) -> tuple[datetime, datetime] | None:
        return session_open_close_utc(d)

    def normalize_ticker(self, raw: str) -> str:
        return normalize_us_ticker(raw)

    def is_valid_ticker(self, raw: str) -> bool:
        return is_valid_us_ticker(raw)

    def compute_tax(
        self,
        *,
        side: str,
        gross_value: float,
        commission_bps: float | None = None,
    ) -> TaxBreakdown:
        return compute_us_tax(side=side, gross_value=gross_value, commission_bps=commission_bps)
