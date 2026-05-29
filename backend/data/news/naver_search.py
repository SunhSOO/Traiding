"""Naver Search adapter — news vertical.

API docs: https://developers.naver.com/docs/serviceapi/search/news/news.md
Quota: 25,000 calls/day free with X-Naver-Client-Id + Secret headers.
Returns the most recent 1,000 results for a query (paging via
``start``/``display``).

Response (JSON)::

    {"items": [
        {"title": "<b>삼성전자</b>, ...",
         "originallink": "https://...", "link": "...",
         "description": "<b>...</b>", "pubDate": "Mon, 15 Jan 2024 14:00:00 +0900"},
        ...
    ]}

Naver wraps highlighted terms in <b>…</b>; we strip those plus the
&quot;/&amp; entities before persistence. ``originallink`` (the
publisher's own URL) is preferred over the Naver-aggregated ``link``
because it dedups cleanly across Naver Search runs.
"""
from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Callable, Optional

import httpx

from core.config import get_settings
from core.logging import get_logger
from data.news.types import NewsArticleRow

log = get_logger(__name__)

NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
DEFAULT_DISPLAY = 100
MAX_START = 1000           # Naver hard cap on offset+display
_TAG_RE = re.compile(r"</?b>", re.IGNORECASE)


def fetch_naver_news(
    *,
    query: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    http_get: Optional[Callable[[str, dict, dict], httpx.Response]] = None,
    display: int = DEFAULT_DISPLAY,
    max_pages: int = 10,
) -> list[NewsArticleRow]:
    """Return up to 1000 recent articles matching ``query``.

    Naver Search has no time-window parameter; we page through the
    most-recent results until ``max_pages`` or the API returns < display.
    """
    settings = get_settings()
    cid = client_id or settings.naver_client_id
    sec = client_secret or settings.naver_client_secret.get_secret_value()
    if not cid or not sec:
        log.warning("naver.no_credentials")
        return []

    headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec}
    getter = http_get or (lambda u, p, h: httpx.get(u, params=p, headers=h, timeout=15))
    out: list[NewsArticleRow] = []
    start = 1
    for _ in range(max_pages):
        if start > MAX_START:
            break
        params = {
            "query": query,
            "display": display,
            "start": start,
            "sort": "date",
        }
        try:
            resp = getter(NAVER_URL, params, headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("naver.fetch_failed", query=query, start=start, error=str(e))
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            row = _parse_naver(item)
            if row is not None:
                out.append(row)

        if len(items) < display:
            break
        start += display

    return out


def _parse_naver(item: dict) -> NewsArticleRow | None:
    title_raw = item.get("title") or ""
    desc_raw = item.get("description") or ""
    title = _clean(title_raw)
    desc = _clean(desc_raw)
    if not title:
        return None

    url = (item.get("originallink") or item.get("link") or "").strip() or None
    pub_raw = item.get("pubDate") or ""
    published_ts = _parse_naver_pubdate(pub_raw)
    if published_ts is None:
        return None
    publisher = _publisher_from_url(url) if url else None

    return NewsArticleRow(
        source="naver_search",
        title=title,
        published_ts=published_ts,
        language="ko",
        publisher=publisher,
        url=url,
        summary=desc or None,
        as_of_ts=published_ts,
    )


def _clean(s: str) -> str:
    s = _TAG_RE.sub("", s)
    s = html.unescape(s).strip()
    return re.sub(r"\s+", " ", s)


def _parse_naver_pubdate(raw: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _publisher_from_url(url: str) -> str | None:
    """Crude domain extraction — good enough for dedup grouping."""
    m = re.match(r"https?://(?:www\.|news\.)?([^/]+)/", url)
    return m.group(1) if m else None
