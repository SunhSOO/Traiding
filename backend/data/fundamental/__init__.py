"""Company-financials ingestion — DART (KR) + SEC EDGAR (US).

Adapters normalise both into a single concept taxonomy (see
``concepts.py``) so downstream ratio / valuation code can ask for
"REVENUE" or "OPERATING_INCOME" without caring whether the source
was K-IFRS or US-GAAP.
"""
from __future__ import annotations

from data.fundamental.loader import sync_financials
from data.fundamental.types import FinancialFactRow

__all__ = ["sync_financials", "FinancialFactRow"]
