"""US financials adapter — SEC EDGAR XBRL company-concepts API.

We use the SEC's free JSON API, NOT the heavier filings-download path:

    GET https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json

returns historical values for one concept across every 10-K/10-Q that
company has filed. That's the perfect shape for our `financial_facts`
table: one network call per (ticker, concept) gives us 10+ years of
quarterly + annual rows in one go.

SEC requires a descriptive ``User-Agent`` header (with contact info)
on every request. We read it from settings.sec_user_agent.

Rate limit: 10 requests/sec/IP. We sleep 0.11 s between calls and rely
on Tenacity for retry on 429.
"""
from __future__ import annotations

import time
from datetime import UTC, date as DateType, datetime
from typing import Callable, Optional

import httpx

from core.config import get_settings
from core.logging import get_logger
from core.types import Market
from data.fundamental.concepts import SEC_MAP
from data.fundamental.types import FinancialFactRow

log = get_logger(__name__)

SEC_BASE = "https://data.sec.gov/api/xbrl"
SEC_THROTTLE_SECONDS = 0.11   # 10 req/s ceiling


def fetch_us_financials(
    *,
    cik: str,
    ticker: str,
    concepts: Optional[list[str]] = None,
    http_get: Optional[Callable[[str, dict], httpx.Response]] = None,
    user_agent: Optional[str] = None,
) -> list[FinancialFactRow]:
    """Pull ALL historical values for ``concepts`` for one CIK.

    Parameters
    ----------
    cik : str
        10-digit zero-padded CIK (the API tolerates short forms too).
    ticker : str
        Canonical ticker used as the key in `financial_facts`.
    concepts : list[str], optional
        Canonical concept codes to fetch. Defaults to all entries in
        :data:`data.fundamental.concepts.SEC_MAP`.
    http_get : callable, optional
        Injected for tests. Defaults to ``httpx.get``.
    user_agent : str, optional
        Defaults to settings.sec_user_agent. SEC requires this header.
    """
    settings = get_settings()
    ua = user_agent or settings.sec_user_agent
    if not ua:
        log.warning("edgar.no_user_agent")
        return []

    cik_str = str(cik).lstrip("0").zfill(10)
    headers = {"User-Agent": ua, "Accept": "application/json"}

    targets = concepts or list(SEC_MAP.keys())
    getter = http_get or (lambda u, h: httpx.get(u, headers=h, timeout=30))

    out: list[FinancialFactRow] = []
    for canonical in targets:
        raw_tags = SEC_MAP.get(canonical)
        if not raw_tags:
            continue

        for raw_tag in raw_tags:
            url = f"{SEC_BASE}/companyconcept/CIK{cik_str}/us-gaap/{raw_tag}.json"
            try:
                resp = getter(url, headers)
            except Exception as e:
                log.warning("edgar.fetch_failed", concept=canonical, tag=raw_tag, error=str(e))
                continue
            if resp.status_code == 404:
                # This tag wasn't reported; try the next raw_tag candidate.
                continue
            try:
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.warning("edgar.response_bad", concept=canonical, tag=raw_tag, error=str(e))
                continue

            rows = _flatten_concept_response(data, canonical, ticker, raw_tag)
            out.extend(rows)
            # SEC returns the most useful series under whichever raw_tag
            # the company actually files; break on first non-empty hit
            # so we don't double-count.
            if rows:
                break
            time.sleep(SEC_THROTTLE_SECONDS)
        time.sleep(SEC_THROTTLE_SECONDS)

    return out


def _flatten_concept_response(
    data: dict,
    canonical: str,
    ticker: str,
    raw_tag: str,
) -> list[FinancialFactRow]:
    """Walk the company-concept JSON and emit FinancialFactRow rows.

    SEC payload shape::

        {
          "units": {
            "USD": [
              {"start": "2023-01-01", "end": "2023-03-31",
               "val": 12345678,
               "fp": "Q1", "fy": 2023,
               "form": "10-Q", "filed": "2023-05-01", ...},
              ...
            ]
          }
        }
    """
    rows: list[FinancialFactRow] = []
    units = data.get("units", {})
    for currency, facts in units.items():
        # SEC unit codes include "USD", "EUR" (currency), and non-currency
        # values like "shares", "pure", "Year". The `currency` column is
        # VARCHAR(3); skip anything that isn't a 3-letter currency code.
        ccy = currency.split("/")[0]
        if len(ccy) != 3 or not ccy.isalpha():
            continue
        ccy = ccy.upper()
        for fact in facts:
            end_str = fact.get("end")
            try:
                period_end = datetime.strptime(end_str, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue
            form = (fact.get("form") or "").upper()
            fp = (fact.get("fp") or "").upper()  # 'Q1'..'Q3', 'FY'
            if fp == "FY" or form == "10-K":
                period_kind = "A"
            elif fp in ("Q1", "Q2", "Q3") or form == "10-Q":
                period_kind = "Q"
            else:
                # Skip unusual forms (8-K, S-1, etc.) which can show up here.
                continue

            value = fact.get("val")
            if value is None:
                continue

            filed_str = fact.get("filed")
            try:
                as_of = datetime.strptime(filed_str, "%Y-%m-%d").replace(tzinfo=UTC)
            except (TypeError, ValueError):
                as_of = datetime.combine(period_end, datetime.min.time(), tzinfo=UTC)

            rows.append(FinancialFactRow(
                market=Market.US, ticker=ticker, concept=canonical,
                period_end=period_end, period_kind=period_kind,
                value=float(value), currency=ccy,
                source="edgar", raw_concept=raw_tag,
                as_of_ts=as_of,
            ))
    return rows
