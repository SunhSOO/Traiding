"""Regulatory-filing ingestion — DART (KR) + EDGAR (US).

Only filing metadata (date, type, title, source URL) is loaded by
default. Body text is fetched lazily by the information-analysis
module when it actually wants to classify a filing — that keeps the
ingestion bandwidth and disk footprint manageable on a single PC.
"""
from __future__ import annotations

from data.disclosures.loader import sync_disclosures
from data.disclosures.types import DisclosureRow

__all__ = ["sync_disclosures", "DisclosureRow"]
