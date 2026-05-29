"""US disclosure adapter — SEC EDGAR submissions JSON.

We hit the company's submissions index:
    GET https://data.sec.gov/submissions/CIK{cik}.json

Returns a JSON object with two ``filings`` blocks:
- ``recent``: the last 1000 filings, fully inlined
- ``files``: array of paged JSON URLs for older filings

For our purposes (latest news + recent context for the info module)
``recent`` is enough. Phase 1.6 backfill can walk ``files``.

Canonical type mapping:
- 10-K → ANNUAL
- 10-Q → QUARTERLY
- 8-K → MATERIAL_EVENT
- Form 3 / 4 / 5 → INSIDER
- DEF 14A → OTHER (proxy statements; useful but not in our top tiers)
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable, Optional

import httpx

from core.config import get_settings
from core.logging import get_logger
from core.types import Market
from data.disclosures.types import (
    ANNUAL,
    INSIDER,
    MATERIAL_EVENT,
    OTHER,
    QUARTERLY,
    DisclosureRow,
)

log = get_logger(__name__)

SEC_BASE = "https://data.sec.gov"


def fetch_us_disclosures(
    *,
    cik: str,
    ticker: str,
    http_get: Optional[Callable[[str, dict], httpx.Response]] = None,
    user_agent: Optional[str] = None,
    only_forms: Optional[set[str]] = None,
) -> list[DisclosureRow]:
    """Pull recent SEC submissions for one CIK.

    Parameters
    ----------
    cik : str
        10-digit zero-padded CIK acceptable.
    only_forms : set[str], optional
        Filter to specific form codes ('10-K', '10-Q', '8-K', '4', ...).
        None means all known canonical forms.
    """
    settings = get_settings()
    ua = user_agent or settings.sec_user_agent
    if not ua:
        log.warning("edgar.disclosures.no_user_agent")
        return []

    cik_str = str(cik).lstrip("0").zfill(10)
    url = f"{SEC_BASE}/submissions/CIK{cik_str}.json"
    getter = http_get or (lambda u, h: httpx.get(u, headers=h, timeout=30))
    try:
        resp = getter(url, {"User-Agent": ua, "Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("edgar.submissions_failed", cik=cik_str, error=str(e))
        return []

    recent = (data.get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    primary_docs = recent.get("primaryDocument") or []
    descriptions = recent.get("primaryDocDescription") or []

    out: list[DisclosureRow] = []
    for i, accession in enumerate(accessions):
        form = forms[i] if i < len(forms) else ""
        if only_forms is not None and form not in only_forms:
            continue
        canonical = _canonicalize_us_form(form)
        if canonical == OTHER and only_forms is None:
            # Skip non-canonical forms unless caller asked for everything
            continue

        date_str = dates[i] if i < len(dates) else None
        try:
            filing_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        primary = primary_docs[i] if i < len(primary_docs) else ""
        description = descriptions[i] if i < len(descriptions) else ""

        accession_clean = accession.replace("-", "")
        source_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/"
            f"{accession_clean}/{primary}"
        )

        filing_ts = datetime.combine(filing_date, datetime.min.time(), tzinfo=UTC)
        out.append(DisclosureRow(
            market=Market.US,
            ticker=ticker,
            source="edgar",
            source_id=accession,
            filing_date=filing_date,
            filing_type=form,
            filing_type_canonical=canonical,
            title=description or form,
            source_url=source_url,
            filing_ts=filing_ts,
            as_of_ts=filing_ts,
        ))
    return out


def _canonicalize_us_form(form: str) -> str:
    f = (form or "").upper()
    if f.startswith("10-K"):
        return ANNUAL
    if f.startswith("10-Q"):
        return QUARTERLY
    if f.startswith("8-K"):
        return MATERIAL_EVENT
    if f in ("3", "4", "5", "3/A", "4/A", "5/A"):
        return INSIDER
    return OTHER
