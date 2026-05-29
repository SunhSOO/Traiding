"""MarketAdapter abstract base.

A market adapter owns everything that varies between trading venues:
trading-day calendars, transaction taxes, ticker format, base
currency. Higher layers (decision engine, sizer, brokers) consume
adapters via the `get_adapter()` registry; they never branch on
`market == "KR"`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date as DateType, datetime

from core.types import Market


@dataclass(frozen=True)
class TaxBreakdown:
    """Itemised tax/fee breakdown for one trade.

    `total` is always the sum of the components — never compute it
    twice in the caller; trust this object.
    """
    commission: float
    transaction_tax: float
    other_fees: float

    @property
    def total(self) -> float:
        return self.commission + self.transaction_tax + self.other_fees


class MarketAdapter(ABC):
    """Per-market contract. All methods are pure / deterministic so
    they're safe to call inside the as_of-pinned context manager."""

    market: Market

    # ── Identity ──
    @property
    @abstractmethod
    def base_currency(self) -> str:
        """ISO 4217 code for the venue's settlement currency (e.g. 'KRW', 'USD')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable market name for UI/logs."""

    # ── Calendar ──
    @abstractmethod
    def is_trading_day(self, d: DateType) -> bool:
        """Whether `d` is a trading day in this market."""

    @abstractmethod
    def previous_trading_day(self, d: DateType) -> DateType:
        """The most recent trading day strictly before `d`."""

    @abstractmethod
    def next_trading_day(self, d: DateType) -> DateType:
        """The next trading day strictly after `d`."""

    @abstractmethod
    def session_open_close_utc(self, d: DateType) -> tuple[datetime, datetime] | None:
        """Returns (open_utc, close_utc) for `d`, or None if not a
        trading day. Both timestamps are timezone-aware UTC."""

    # ── Ticker ──
    @abstractmethod
    def normalize_ticker(self, raw: str) -> str:
        """Canonical form: KR is zero-padded 6-digit numeric; US is uppercase letters."""

    @abstractmethod
    def is_valid_ticker(self, raw: str) -> bool:
        """Cheap syntactic validation — doesn't check if the ticker
        actually exists in the universe."""

    # ── Tax ──
    @abstractmethod
    def compute_tax(
        self,
        *,
        side: str,
        gross_value: float,
        commission_bps: float | None = None,
    ) -> TaxBreakdown:
        """Compute commission + transaction tax + other fees for a trade.

        `side` is 'BUY' or 'SELL'. `gross_value` is the trade notional in
        the market's base currency. `commission_bps` lets the caller
        override the default broker commission for that venue.
        """
