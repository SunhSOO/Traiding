"""US universe fetcher — S&P 500 + NASDAQ-100.

FinanceDataReader's ``StockListing('S&P500')`` and
``StockListing('NASDAQ100')`` are the primary sources because they're
free, kept up-to-date, and return DataFrames with ticker + name +
sector ready to use.

Fallback (not yet implemented): scrape Wikipedia's S&P 500 page if
FinanceDataReader fails. We defer that until we actually see failures
in production — adding crawl code speculatively just creates a
maintenance burden.
"""
from __future__ import annotations

from datetime import date as DateType
from typing import Callable, Optional

from core.logging import get_logger
from core.types import Market
from data.universe.types import SecurityInfo
from markets.us.ticker import normalize_us_ticker

log = get_logger(__name__)


def fetch_us_universe(
    as_of_date: DateType,
    *,
    fetch_listing: Callable[[str], list[dict]],
) -> list[SecurityInfo]:
    """Return SecurityInfo for every SP500 ∪ NASDAQ100 ticker.

    Parameters
    ----------
    as_of_date : date
        Snapshot date. FinanceDataReader returns "as of today"; we
        record this as the snapshot's ``valid_from`` later.
    fetch_listing : (index_code) -> list[{symbol, name, sector, industry, ...}]
        Wraps ``fdr.StockListing``. ``index_code`` is 'S&P500' or
        'NASDAQ100'. Test stubs return a static list.
    """
    membership: dict[str, dict] = {}

    for index_code, listing_key in [("SP500", "S&P500"), ("NASDAQ100", "NASDAQ100")]:
        try:
            rows = fetch_listing(listing_key)
        except Exception as e:
            log.warning("us.listing_fetch_failed", index=index_code, error=str(e))
            rows = []

        for row in rows:
            raw_symbol = row.get("Symbol") or row.get("symbol") or row.get("Ticker")
            if not raw_symbol:
                continue
            try:
                ticker = normalize_us_ticker(raw_symbol)
            except ValueError:
                log.warning("us.ticker_unparseable", raw=raw_symbol)
                continue

            entry = membership.setdefault(ticker, {
                "name": row.get("Name") or row.get("name") or ticker,
                "sector": row.get("Sector") or row.get("sector"),
                "industry": row.get("Industry") or row.get("industry"),
                "exchange": row.get("Exchange") or row.get("exchange"),
                "indices": set(),
            })
            entry["indices"].add(index_code)
            # Newer enrichment wins for sector/industry (avoid losing data
            # if the first source had it as null).
            for k in ("name", "sector", "industry", "exchange"):
                val = row.get(k) or row.get(k.capitalize())
                if val and not entry.get(k):
                    entry[k] = val

        log.info("us.listing_fetched", index=index_code, count=len(rows))

    out: list[SecurityInfo] = []
    for ticker, e in membership.items():
        out.append(
            SecurityInfo(
                market=Market.US,
                ticker=ticker,
                name=e.get("name") or ticker,
                sector=e.get("sector"),
                industry=e.get("industry"),
                exchange=(e.get("exchange") or "").upper() or None,
                currency="USD",
                index_codes=frozenset(e["indices"]),
            )
        )
    return out


# ── Production wiring ──
def build_default_us_fetcher() -> dict[str, Callable]:
    """Real FinanceDataReader-backed fetchers."""
    import FinanceDataReader as fdr

    def _listing(index_code: str) -> list[dict]:
        df = fdr.StockListing(index_code)
        return df.to_dict("records") if df is not None else []

    return {"fetch_listing": _listing}
