"""Normalised universe types — the shape adapters return."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as DateType
from typing import Optional

from core.types import Market


@dataclass(frozen=True)
class SecurityInfo:
    """One ticker's metadata, normalised across markets.

    Adapters fill in what they know; missing optional fields stay None.
    The loader merges multiple adapters' SecurityInfo objects when a
    ticker appears in more than one source (e.g. yfinance + Wikipedia).
    """

    market: Market
    ticker: str                          # canonical form (zero-padded 6 digits for KR)
    name: str
    name_en: Optional[str] = None
    isin: Optional[str] = None
    cik: Optional[str] = None
    corp_code: Optional[str] = None      # DART corp_code for KR
    exchange: Optional[str] = None       # KOSPI / KOSDAQ / NYSE / NASDAQ
    sector: Optional[str] = None
    industry: Optional[str] = None
    listed_date: Optional[DateType] = None
    delisted_date: Optional[DateType] = None
    currency: str = ""                   # KRW / USD — required, but adapters fill
    # Which indices this ticker belongs to: KOSPI200, KOSDAQ150, SP500, NASDAQ100
    index_codes: frozenset[str] = field(
        default_factory=frozenset,
        repr=False,
    )

    def merged_with(self, other: "SecurityInfo") -> "SecurityInfo":
        """Combine two records for the same (market, ticker), preferring
        non-None values from ``self`` and falling back to ``other``."""
        if (self.market, self.ticker) != (other.market, other.ticker):
            raise ValueError(
                f"refusing to merge across keys: "
                f"{self.market}:{self.ticker} vs {other.market}:{other.ticker}"
            )

        def pick(a, b):
            return a if a is not None and a != "" else b

        return SecurityInfo(
            market=self.market,
            ticker=self.ticker,
            name=pick(self.name, other.name) or self.name,
            name_en=pick(self.name_en, other.name_en),
            isin=pick(self.isin, other.isin),
            cik=pick(self.cik, other.cik),
            corp_code=pick(self.corp_code, other.corp_code),
            exchange=pick(self.exchange, other.exchange),
            sector=pick(self.sector, other.sector),
            industry=pick(self.industry, other.industry),
            listed_date=pick(self.listed_date, other.listed_date),
            delisted_date=pick(self.delisted_date, other.delisted_date),
            currency=pick(self.currency, other.currency) or "",
            index_codes=self.index_codes | other.index_codes,
        )
