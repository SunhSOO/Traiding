"""Macro series payload returned by adapters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType, datetime
from typing import Optional


@dataclass(frozen=True)
class MacroPoint:
    series_code: str    # internal code, e.g. 'FX_USDKRW'
    ts: DateType
    value: float
    source: str
    as_of_ts: Optional[datetime] = None
