"""End-of-day OHLCV ingestion for KR + US tickers.

Adapters wrap pykrx (KR) and yfinance (US) so the loader can be
mocked for unit testing. Bulk semantics: the loader operates on a
list of tickers and a date range, bucketing API calls efficiently.

`as_of_ts` on each row is computed as the bar's session-close UTC
plus a small "data available" lag (default 2 minutes). This is what
the as_of-aware reader filters on later.
"""
from __future__ import annotations

from data.price.loader import sync_daily_prices
from data.price.types import DailyBar

__all__ = ["sync_daily_prices", "DailyBar"]
