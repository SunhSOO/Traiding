"""Normalised financial-fact type returned by adapters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType, datetime
from typing import Optional

from core.types import Market


@dataclass(frozen=True)
class FinancialFactRow:
    """One concept's value for one period for one ticker."""

    market: Market
    ticker: str
    concept: str                 # canonical concept code (see concepts.py)
    period_end: DateType
    period_kind: str             # 'Q' | 'A'
    value: float
    currency: str
    source: str
    raw_concept: Optional[str] = None
    as_of_ts: Optional[datetime] = None
