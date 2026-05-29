"""Normalised price-bar types returned by adapters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType, datetime
from typing import Optional

from core.types import Market


@dataclass(frozen=True)
class DailyBar:
    """One end-of-day OHLCV row, source-agnostic."""

    market: Market
    ticker: str
    trade_date: DateType
    open: float
    high: float
    low: float
    close: float
    volume: int
    adj_close: Optional[float] = None
    foreign_net: Optional[float] = None        # KR only
    institution_net: Optional[float] = None    # KR only
    source: str = "unknown"
    as_of_ts: Optional[datetime] = None        # filled by adapter / loader
