"""US ticker → SEC CIK enrichment.

SEC publishes a public mapping of all US-listed tickers to their
10-digit CIK at:

    https://www.sec.gov/files/company_tickers.json

Shape::

    {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
     "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
     ...}

We require a descriptive ``User-Agent`` (sec_user_agent setting) on
the request — SEC rejects anonymous clients with HTTP 403.

Class-share tickers (BRK.B etc.) appear in SEC as "BRK-B"; we
normalise to our canonical form during the lookup.
"""
from __future__ import annotations

from typing import Callable, Optional

import httpx

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def fetch_sec_ticker_cik_map(
    *,
    user_agent: Optional[str] = None,
    http_get: Optional[Callable[[str, dict], httpx.Response]] = None,
) -> dict[str, str]:
    """Return ``{ticker: cik_10_digit_zero_padded}``."""
    settings = get_settings()
    ua = user_agent or settings.sec_user_agent
    if not ua:
        log.warning("sec.tickers.no_user_agent")
        return {}

    headers = {"User-Agent": ua, "Accept": "application/json"}
    getter = http_get or (lambda u, h: httpx.get(u, headers=h, timeout=30))

    try:
        resp = getter(SEC_TICKERS_URL, headers)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("sec.tickers.fetch_failed", error=str(e))
        return {}

    if not isinstance(data, dict):
        log.warning("sec.tickers.unexpected_shape", type=type(data).__name__)
        return {}

    out: dict[str, str] = {}
    for entry in data.values():
        try:
            ticker_raw = str(entry["ticker"]).strip().upper()
            cik_int = int(entry["cik_str"])
        except (KeyError, ValueError, TypeError):
            continue
        # SEC uses "BRK-B" for class shares; canonical in our DB is "BRK.B"
        canonical = ticker_raw.replace("-", ".")
        cik_padded = f"{cik_int:010d}"
        out[canonical] = cik_padded
    return out


def apply_ciks_to_securities(session, cik_map: dict[str, str]) -> int:
    """Set ``securities.cik`` for every US ticker that the SEC map covers.

    Returns count of rows updated. Idempotent."""
    if not cik_map:
        return 0

    from sqlalchemy import select

    from core.models.universe import Security

    updated = 0
    stmt = select(Security).where(Security.market == "US")
    for sec in session.scalars(stmt):
        new = cik_map.get(sec.ticker)
        if new and sec.cik != new:
            sec.cik = new
            updated += 1
    session.flush()
    log.info("sec.cik.applied", updated=updated, total=len(cik_map))
    return updated
