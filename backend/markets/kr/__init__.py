"""KR market adapter — KRX (KOSPI + KOSDAQ)."""
from __future__ import annotations

from datetime import date as DateType, datetime

from core.types import Market
from markets.base import MarketAdapter, TaxBreakdown
from markets.kr.calendar import KRX_TZ, is_trading_day, next_trading_day, previous_trading_day, session_open_close_utc
from markets.kr.tax import compute_kr_tax
from markets.kr.ticker import is_valid_kr_ticker, normalize_kr_ticker


class KRMarketAdapter(MarketAdapter):
    market = Market.KR

    @property
    def base_currency(self) -> str:
        return "KRW"

    @property
    def display_name(self) -> str:
        return "한국거래소 (KOSPI + KOSDAQ)"

    def is_trading_day(self, d: DateType) -> bool:
        return is_trading_day(d)

    def previous_trading_day(self, d: DateType) -> DateType:
        return previous_trading_day(d)

    def next_trading_day(self, d: DateType) -> DateType:
        return next_trading_day(d)

    def session_open_close_utc(self, d: DateType) -> tuple[datetime, datetime] | None:
        return session_open_close_utc(d)

    def normalize_ticker(self, raw: str) -> str:
        return normalize_kr_ticker(raw)

    def is_valid_ticker(self, raw: str) -> bool:
        return is_valid_kr_ticker(raw)

    def compute_tax(
        self,
        *,
        side: str,
        gross_value: float,
        commission_bps: float | None = None,
    ) -> TaxBreakdown:
        return compute_kr_tax(side=side, gross_value=gross_value, commission_bps=commission_bps)
