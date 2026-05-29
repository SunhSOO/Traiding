"""GDELT adapter — Global Database of Events, Language, and Tone.

GDELT 2.0 publishes a 15-minute-cadence stream of every news article
it can find, with metadata (publisher domain, language, tone score,
URL). It's free, public, and goes back to 2015 for the v2 schema.

For ticker-level news we use the GDELT DOC 2.0 API:

    https://api.gdeltproject.org/api/v2/doc/doc?
        query=<query>&mode=ArtList&format=JSON
        &startdatetime=<UTC YYYYMMDDHHMMSS>
        &enddatetime=<UTC YYYYMMDDHHMMSS>
        &maxrecords=250

Returns at most 250 articles per call; we page by date-windowing.
Response shape::

    {"articles": [
        {"url": ..., "url_mobile": ..., "title": ...,
         "seendate": "20240115T140000Z",
         "socialimage": ..., "domain": "bloomberg.com",
         "language": "English", "sourcecountry": "United States"},
        ...
    ]}

Tone score is part of GDELT's GKG enrichment but not exposed by the
DOC API; we'd need separate GKG queries to get tones. Phase 2 LLM
classifier scores tone itself, so we can ignore for now.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable, Optional

import httpx

from core.logging import get_logger
from data.news.types import NewsArticleRow

log = get_logger(__name__)

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_PAGE_MAX = 250


def fetch_gdelt(
    *,
    query: str,
    start: datetime,
    end: datetime,
    http_get: Optional[Callable[[str, dict], httpx.Response]] = None,
    languages: Optional[list[str]] = None,
    max_pages: int = 20,
    window_hours: int = 24,
) -> list[NewsArticleRow]:
    """Return GDELT articles matching ``query`` over [start, end].

    Time-windows the search into ``window_hours`` chunks because the
    DOC API returns at most ``GDELT_PAGE_MAX`` rows per call.

    Parameters
    ----------
    query : str
        GDELT query — typically a company name or ``"<ticker>"`` for US.
    languages : list[str], optional
        Restrict to specific GDELT language names (e.g. ``["English", "Korean"]``).
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("GDELT requires tz-aware start/end")

    getter = http_get or (lambda u, p: httpx.get(u, params=p, timeout=30))
    out: list[NewsArticleRow] = []
    pages = 0
    cursor = start

    while cursor < end and pages < max_pages:
        chunk_end = min(cursor + timedelta(hours=window_hours), end)
        params = {
            "query": _build_query(query, languages),
            "mode": "ArtList",
            "format": "JSON",
            "startdatetime": cursor.strftime("%Y%m%d%H%M%S"),
            "enddatetime": chunk_end.strftime("%Y%m%d%H%M%S"),
            "maxrecords": GDELT_PAGE_MAX,
            "sort": "DateDesc",
        }
        try:
            resp = getter(GDELT_URL, params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("gdelt.fetch_failed", cursor=str(cursor), error=str(e))
            break

        for art in data.get("articles", []):
            row = _parse_gdelt(art)
            if row is not None:
                out.append(row)

        cursor = chunk_end
        pages += 1

    return out


def _build_query(query: str, languages: Optional[list[str]]) -> str:
    parts = [query]
    if languages:
        # GDELT language qualifier: sourcelang:English (OR sourcelang:Korean)
        lang_clause = " OR ".join(f"sourcelang:{l}" for l in languages)
        parts.append(f"({lang_clause})")
    return " ".join(parts)


def _parse_gdelt(art: dict) -> NewsArticleRow | None:
    title = (art.get("title") or "").strip()
    url = (art.get("url") or "").strip() or None
    seendate = art.get("seendate")
    if not title or not seendate:
        return None
    try:
        published_ts = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    domain = (art.get("domain") or "").strip() or None
    lang_long = (art.get("language") or "").lower()
    lang_short = _short_lang_code(lang_long)

    return NewsArticleRow(
        source="gdelt",
        title=title,
        published_ts=published_ts,
        language=lang_short,
        publisher=domain,
        url=url,
        summary=None,
        body_text=None,
        as_of_ts=published_ts,
    )


def _short_lang_code(gdelt_lang: str) -> str:
    return {
        "english": "en",
        "korean": "ko",
        "japanese": "ja",
        "chinese": "zh",
        "spanish": "es",
        "french": "fr",
        "german": "de",
    }.get(gdelt_lang, "en")
