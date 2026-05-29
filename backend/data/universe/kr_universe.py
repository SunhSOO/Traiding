"""KR universe fetcher — KOSPI 200 + KOSDAQ 150 via pykrx.

pykrx scrapes KRX for index constituents. We wrap its calls so the
loader can mock the wrapper for unit testing.

Refresh logic:
- Pull current KOSPI 200 constituents → mark KOSPI200 membership
- Pull current KOSDAQ 150 constituents → mark KOSDAQ150 membership
- Pull full KOSPI + KOSDAQ ticker lists with names → fill in metadata
- Tickers can belong to BOTH (rare but possible across mid-cap moves)

We do NOT call DART here for corp_code — that's a separate enrichment
step in `data.fundamental.kr_dart` because it needs the DART API key.
"""
from __future__ import annotations

from datetime import date as DateType
from typing import Callable, Optional

from core.logging import get_logger
from core.types import Market
from data.universe.types import SecurityInfo
from markets.kr.ticker import normalize_kr_ticker

log = get_logger(__name__)

# Type alias for an injected pykrx-like dependency. Production passes
# the real pykrx callables; tests pass mocks.
PyKrxFetcher = Callable[[str, str], list[dict]]


def fetch_kr_universe(
    as_of_date: DateType,
    *,
    fetch_index_portfolio: Callable[[str, str], list[str]],
    fetch_market_tickers: Callable[[str, str], list[str]],
    fetch_ticker_name: Callable[[str], str],
    fetch_market_for_ticker: Callable[[str], str],
) -> list[SecurityInfo]:
    """Return SecurityInfo for every KOSPI200 ∪ KOSDAQ150 ticker.

    Parameters
    ----------
    as_of_date : date
        The KRX trading date to query (pykrx uses YYYYMMDD strings).
    fetch_index_portfolio : (yyyymmdd, index_code) -> list[ticker]
        Wraps ``pykrx.stock.get_index_portfolio_deposit_file``. Pass the
        real function in production; pass a stub returning a static list
        in tests.
    fetch_market_tickers : (yyyymmdd, market) -> list[ticker]
        Wraps ``pykrx.stock.get_market_ticker_list``.
        ``market`` is 'KOSPI' or 'KOSDAQ'.
    fetch_ticker_name : (ticker) -> name
        Wraps ``pykrx.stock.get_market_ticker_name``.
    fetch_market_for_ticker : (ticker) -> 'KOSPI'|'KOSDAQ'
        Decides which exchange the ticker actually lives on (in case a
        ticker shows up in both index lists, which can happen during
        upmarket promotions).
    """
    yyyymmdd = as_of_date.strftime("%Y%m%d")

    # 1. Index constituents → maps ticker → set of indices
    membership: dict[str, set[str]] = {}
    for index_code, pykrx_index_id in [("KOSPI200", "1028"), ("KOSDAQ150", "2203")]:
        try:
            tickers = fetch_index_portfolio(yyyymmdd, pykrx_index_id)
        except Exception as e:
            log.warning("kr.index_fetch_failed", index=index_code, error=str(e))
            tickers = []
        for raw in tickers:
            t = normalize_kr_ticker(raw)
            membership.setdefault(t, set()).add(index_code)
        log.info("kr.index_fetched", index=index_code, count=len(tickers))

    # 2. Enrich each ticker with name + exchange
    out: list[SecurityInfo] = []
    for ticker, indices in membership.items():
        try:
            name = fetch_ticker_name(ticker) or ticker
        except Exception as e:
            log.warning("kr.name_fetch_failed", ticker=ticker, error=str(e))
            name = ticker

        try:
            exchange = fetch_market_for_ticker(ticker) or ""
        except Exception:
            exchange = ""

        out.append(
            SecurityInfo(
                market=Market.KR,
                ticker=ticker,
                name=name,
                exchange=exchange.upper() or None,
                currency="KRW",
                index_codes=frozenset(indices),
            )
        )

    return out


# ── Production wiring ──
def build_default_kr_fetcher() -> dict[str, Callable]:
    """Return a dict of real pykrx callables suitable for passing to
    :func:`fetch_kr_universe`. Imports pykrx lazily so test envs
    without it can still import this module.
    """
    from pykrx import stock

    def _market_for_ticker(ticker: str) -> str:
        # pykrx exposes get_market_ticker_list per market but not the
        # inverse. We probe each market list (cheap, cached upstream).
        return stock.get_market_ticker_name(ticker) and (
            "KOSPI" if ticker in stock.get_market_ticker_list(market="KOSPI") else "KOSDAQ"
        )

    return {
        "fetch_index_portfolio": stock.get_index_portfolio_deposit_file,
        "fetch_market_tickers": stock.get_market_ticker_list,
        "fetch_ticker_name": stock.get_market_ticker_name,
        "fetch_market_for_ticker": _market_for_ticker,
    }
