"""Market-specific adapters: KR (KRX) and US (NYSE/NASDAQ).

Every market-aware function in the system goes through one of these
adapters so that adding a new market later (e.g. JP, EU) requires
only a new subpackage, never changes to higher-level code.

`get_adapter(Market)` returns the per-market adapter object. Use it
from any code that needs trading-day awareness, tax math, ticker
normalization, or currency context.
"""
from __future__ import annotations

from core.types import Market
from markets.base import MarketAdapter
from markets.kr import KRMarketAdapter
from markets.us import USMarketAdapter

_REGISTRY: dict[Market, MarketAdapter] = {
    Market.KR: KRMarketAdapter(),
    Market.US: USMarketAdapter(),
}


def get_adapter(market: Market | str) -> MarketAdapter:
    """Look up the adapter for a market. Accepts either the enum or
    its string value to make API/HTTP boundaries painless."""
    if isinstance(market, str):
        market = Market(market.upper())
    try:
        return _REGISTRY[market]
    except KeyError as e:
        raise ValueError(f"No adapter registered for market {market}") from e


def supported_markets() -> list[Market]:
    return list(_REGISTRY.keys())


__all__ = ["MarketAdapter", "get_adapter", "supported_markets"]
