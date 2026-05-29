"""Universe loaders — what tickers exist in KOSPI200/KOSDAQ150/SP500/NASDAQ100.

Drives every downstream module: only the tickers in the active
universe get price/financial/news ingested, and only those tickers
are eligible for trading.

We persist BOTH:
- `securities` master row (one per (market, ticker))
- `universe_membership` rows recording when each ticker was in which
  index — preserves survivorship-free history.

Refresh cadence: monthly (KR index reshuffles are quarterly, US
indices are continuously rebalanced; daily checks waste API calls).
"""
from __future__ import annotations

from data.universe.loader import sync_universe
from data.universe.types import SecurityInfo

__all__ = ["sync_universe", "SecurityInfo"]
