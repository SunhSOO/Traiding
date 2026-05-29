"""Wayback Machine adapter — historical URL snapshot recovery.

archive.org's Wayback Machine offers two free APIs:

1. **CDX server** — for "did snapshot exist; if so when":
   ``http://web.archive.org/cdx/search/cdx?url={url}&output=json``
2. **Memento** — for "give me the snapshot closest to timestamp T":
   ``https://web.archive.org/web/{yyyymmddhhmmss}/{url}``

We use both: CDX to discover snapshots, Memento to fetch their bodies.

This adapter is meant for **targeted backfill** — given a specific
article URL we know about (e.g. from a BIGKinds tag link that 404s
today), recover the historical version. Bulk discovery of new
historical articles via Wayback is not feasible — there's no per-
publisher search.

Use case: Phase 2 LLM classifier finds an article in BIGKinds with
no body, attempts to fetch the original URL, gets 404, then asks
this adapter for the closest Wayback snapshot.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Callable, Optional

import httpx

from core.logging import get_logger

log = get_logger(__name__)

CDX_URL = "http://web.archive.org/cdx/search/cdx"
MEMENTO_BASE = "https://web.archive.org/web"


def list_snapshots(
    url: str,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    http_get: Optional[Callable[[str, dict], httpx.Response]] = None,
    limit: int = 50,
) -> list[dict]:
    """Return CDX rows for ``url`` in [start, end].

    Each row is a dict: {timestamp, original, mime, status, length}.
    Newest snapshots first.
    """
    params = {
        "url": url, "output": "json", "limit": -limit,
        "filter": "statuscode:200",
    }
    if start:
        params["from"] = start.strftime("%Y%m%d%H%M%S")
    if end:
        params["to"] = end.strftime("%Y%m%d%H%M%S")

    getter = http_get or (lambda u, p: httpx.get(u, params=p, timeout=30))
    try:
        resp = getter(CDX_URL, params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("wayback.cdx_failed", url=url, error=str(e))
        return []

    if not data or len(data) < 2:
        return []
    header, *rows = data
    keys = list(header)
    return [dict(zip(keys, row)) for row in rows]


def fetch_snapshot(
    url: str,
    *,
    target_ts: Optional[datetime] = None,
    http_get: Optional[Callable[[str], httpx.Response]] = None,
) -> tuple[str, datetime] | None:
    """Fetch the Memento closest to ``target_ts`` (default: now).

    Returns (html_body, captured_ts) on success, None on miss.
    """
    ts = (target_ts or datetime.now(UTC)).strftime("%Y%m%d%H%M%S")
    memento_url = f"{MEMENTO_BASE}/{ts}/{url}"
    getter = http_get or (lambda u: httpx.get(u, timeout=30, follow_redirects=True))
    try:
        resp = getter(memento_url)
        resp.raise_for_status()
    except Exception as e:
        log.warning("wayback.memento_failed", url=url, error=str(e))
        return None

    # The Wayback adds an `X-Archive-Orig-Date` header on success;
    # we prefer that, fall back to the URL it redirected to.
    captured_str = resp.headers.get("X-Archive-Orig-Date")
    captured = _parse_archive_date(captured_str) or _capture_from_url(str(resp.url)) or datetime.now(UTC)
    return (resp.text, captured)


def _parse_archive_date(s: Optional[str]) -> datetime | None:
    if not s:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        return dt.astimezone(UTC) if dt and dt.tzinfo else (dt.replace(tzinfo=UTC) if dt else None)
    except (TypeError, ValueError):
        return None


def _capture_from_url(url: str) -> datetime | None:
    m = re.search(r"/web/(\d{14})/", url)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None
