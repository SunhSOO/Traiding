"""Normalised disclosure type returned by adapters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType, datetime
from typing import Optional

from core.types import Market


# Canonical filing categories — used by `filing_type_canonical`.
ANNUAL = "ANNUAL"
QUARTERLY = "QUARTERLY"
MATERIAL_EVENT = "MATERIAL_EVENT"        # KR 주요사항보고서 / US 8-K
INSIDER = "INSIDER"                       # KR 지분공시 / US Form 4
OTHER = "OTHER"


@dataclass(frozen=True)
class DisclosureRow:
    """One filing's metadata, normalised across markets."""

    market: Market
    ticker: str
    source: str                   # 'dart' | 'kind' | 'edgar'
    source_id: str                # rcept_no or accession_number
    filing_date: DateType
    filing_type: str              # source's own type code
    filing_type_canonical: str    # ANNUAL/QUARTERLY/MATERIAL_EVENT/INSIDER/OTHER
    title: str
    source_url: Optional[str] = None
    filing_ts: Optional[datetime] = None
    as_of_ts: Optional[datetime] = None
