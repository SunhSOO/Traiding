"""BIGKinds adapter — Korea Press Foundation news archive.

API docs: https://www.bigkinds.or.kr/v2/news/dataApi.do
Free for research; requires application + access key. Once registered,
the key permits queries against 50+ Korean publishers' headlines and
bodies going back to ~1990.

Endpoint shape (POST JSON):

    https://tools.kinds.or.kr/search/news

    {
      "access_key": "...",
      "argument": {
        "query": "삼성전자",
        "published_at": {"from": "2024-01-01", "until": "2024-01-31"},
        "provider": [],          # empty = all
        "category": [],
        "fields": ["title", "content", "provider", "published_at",
                   "byline", "category", "provider_link_page",
                   "tms_raw_stream"],
        "return_size": 1000,
        "return_from": 0
      }
    }

Response carries ``return_object.documents``. We page until either
the source is exhausted or ``hard_limit`` rows are pulled (default
10000 — protects the daily 25 MB quota).

Body-text retrieval is gated by ``include_body``. Default False — we
store summaries only and lazy-fetch bodies for articles the info
module wants to classify (Phase 2).
"""
from __future__ import annotations

from datetime import UTC, date as DateType, datetime, timedelta
from typing import Callable, Optional

import httpx

from core.config import get_settings
from core.logging import get_logger
from data.news.types import NewsArticleRow

log = get_logger(__name__)

BIGKINDS_URL = "https://tools.kinds.or.kr/search/news"
DEFAULT_PAGE_SIZE = 1000


def fetch_bigkinds(
    *,
    query: str,
    start: DateType,
    end: DateType,
    access_key: Optional[str] = None,
    http_post: Optional[Callable[[str, dict], httpx.Response]] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    hard_limit: int = 10000,
    include_body: bool = False,
    providers: Optional[list[str]] = None,
) -> list[NewsArticleRow]:
    """Return BIGKinds articles matching ``query`` in [start, end].

    Parameters
    ----------
    query : str
        BIGKinds query string. Typically a ticker name (e.g. '삼성전자').
        Boolean operators (AND/OR) are supported by the upstream API.
    providers : list[str], optional
        Restrict to specific publisher codes (e.g. ['01100201'] for 조선일보).
        None = all.
    """
    settings = get_settings()
    key = access_key or settings.bigkinds_access_key.get_secret_value()
    if not key:
        log.warning("bigkinds.no_api_key")
        return []

    poster = http_post or (lambda u, p: httpx.post(u, json=p, timeout=60))
    out: list[NewsArticleRow] = []
    offset = 0

    fields = ["title", "provider", "published_at", "byline", "category",
              "provider_link_page", "tms_raw_stream"]
    if include_body:
        fields.append("content")

    while len(out) < hard_limit:
        body = {
            "access_key": key,
            "argument": {
                "query": query,
                "published_at": {
                    "from": start.isoformat(),
                    "until": end.isoformat(),
                },
                "provider": providers or [],
                "category": [],
                "fields": fields,
                "return_size": min(page_size, hard_limit - len(out)),
                "return_from": offset,
            },
        }
        try:
            resp = poster(BIGKINDS_URL, body)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("bigkinds.fetch_failed", query=query, offset=offset, error=str(e))
            break

        docs = (data.get("return_object") or {}).get("documents") or []
        if not docs:
            break

        for doc in docs:
            row = _parse_bigkinds_doc(doc, include_body=include_body)
            if row is not None:
                out.append(row)

        if len(docs) < body["argument"]["return_size"]:
            break  # last page reached
        offset += len(docs)

    return out


def _parse_bigkinds_doc(doc: dict, *, include_body: bool) -> NewsArticleRow | None:
    title = (doc.get("title") or "").strip()
    published_raw = doc.get("published_at") or doc.get("date")
    if not title or not published_raw:
        return None

    published_ts = _parse_bigkinds_ts(published_raw)
    if published_ts is None:
        return None

    publisher = (doc.get("provider") or "").strip() or None
    url = (doc.get("provider_link_page") or "").strip() or None
    raw_stream = (doc.get("tms_raw_stream") or "").strip()
    summary = (raw_stream or doc.get("content_summary") or "")[:4096] or None
    body = (doc.get("content") or "").strip() if include_body else None

    return NewsArticleRow(
        source="bigkinds",
        title=title,
        published_ts=published_ts,
        language="ko",
        publisher=publisher,
        url=url,
        summary=summary,
        body_text=body,
        as_of_ts=published_ts,
    )


def _parse_bigkinds_ts(raw: str) -> datetime | None:
    """BIGKinds returns 'YYYY-MM-DD' or 'YYYYMMDDHHMMSS'. Both → tz=UTC."""
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        except ValueError:
            continue
    return None
