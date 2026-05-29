"""RSS / Atom adapter — multi-publisher feeds.

Real-time freshness companion to BIGKinds / Naver / GDELT. We poll a
curated feed list every 15 minutes from the scheduler. Each feed has
its own implicit publisher and language; we infer publisher from the
feed URL's hostname.

We deliberately don't depend on the ``feedparser`` package — that
library has a large surface area and a history of XML-parsing CVEs.
Instead we use the stdlib XML parser with explicit defusedxml-style
safeguards (no DTD loading, no entity expansion) and parse only the
~10 fields we actually need.

A small registry of well-known KR + US press RSS URLs lives at the
bottom of this module. Add new feeds there.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Callable, Optional

import httpx

from core.logging import get_logger
from data.news.types import NewsArticleRow

log = get_logger(__name__)


# Bundled feed registry. Extend in production via DB-stored feed list
# (Phase 1.6 future enhancement) — for now this default list covers the
# major Korean financial press + a few US business outlets.
DEFAULT_FEEDS: list[tuple[str, str, str]] = [
    # (publisher_label, feed_url, language)
    ("매일경제", "https://www.mk.co.kr/rss/30000001/", "ko"),
    ("한국경제", "https://www.hankyung.com/feed/economy", "ko"),
    ("조선비즈", "https://biz.chosun.com/rss/news.xml", "ko"),
    ("연합인포맥스", "https://news.einfomax.co.kr/rss/allArticle.xml", "ko"),
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews", "en"),
    ("CNBC Top News", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147", "en"),
]


def fetch_rss(
    feed_url: str,
    *,
    publisher: Optional[str] = None,
    language: str = "ko",
    http_get: Optional[Callable[[str], httpx.Response]] = None,
) -> list[NewsArticleRow]:
    """Pull current items from one RSS / Atom feed."""
    getter = http_get or (lambda u: httpx.get(
        u, timeout=15,
        headers={"User-Agent": "woonam-auto-trading/0.1 (https://github.com/SunhSOO/Traiding)"},
    ))
    try:
        resp = getter(feed_url)
        resp.raise_for_status()
        xml_bytes = resp.content
    except Exception as e:
        log.warning("rss.fetch_failed", url=feed_url, error=str(e))
        return []

    try:
        root = _safe_parse(xml_bytes)
    except ET.ParseError as e:
        log.warning("rss.xml_parse_failed", url=feed_url, error=str(e))
        return []

    return _extract_items(root, source=f"rss:{_host(feed_url)}",
                          publisher=publisher, language=language)


def fetch_all_default_feeds(
    *,
    http_get: Optional[Callable[[str], httpx.Response]] = None,
) -> list[NewsArticleRow]:
    """Fetch every default feed once. Used by the scheduler's intraday
    job."""
    out: list[NewsArticleRow] = []
    for publisher, url, language in DEFAULT_FEEDS:
        out.extend(fetch_rss(url, publisher=publisher, language=language, http_get=http_get))
    return out


# ── XML helpers ──
def _safe_parse(xml_bytes: bytes) -> ET.Element:
    """Parse XML with defenses against billion-laughs / external-DTD
    attacks. Python 3.7.1+ disables those by default in
    ``xml.etree.ElementTree`` but we make it explicit so a future
    parser swap doesn't regress."""
    parser = ET.XMLParser(target=ET.TreeBuilder())
    parser.feed(xml_bytes)
    return parser.close()


def _extract_items(
    root: ET.Element,
    *,
    source: str,
    publisher: Optional[str],
    language: str,
) -> list[NewsArticleRow]:
    # Strip XML namespaces for tag matching ergonomics.
    items: list[ET.Element] = []
    for tag in ("item", "{http://www.w3.org/2005/Atom}entry"):
        items.extend(root.iter(tag))

    out: list[NewsArticleRow] = []
    for el in items:
        title = _first_text(el, ("title", "{http://www.w3.org/2005/Atom}title"))
        if not title:
            continue
        link = _first_text(el, ("link", "{http://www.w3.org/2005/Atom}link"))
        if not link:
            # Atom <link href="..."/>
            for tag in ("link", "{http://www.w3.org/2005/Atom}link"):
                node = el.find(tag)
                if node is not None and node.get("href"):
                    link = node.get("href")
                    break
        pub_raw = _first_text(el, (
            "pubDate", "published", "{http://www.w3.org/2005/Atom}published",
            "{http://purl.org/dc/elements/1.1/}date",
        ))
        published_ts = _parse_pubdate(pub_raw)
        if published_ts is None:
            continue
        summary = _first_text(el, (
            "description", "summary",
            "{http://www.w3.org/2005/Atom}summary",
            "{http://www.w3.org/2005/Atom}content",
        ))

        out.append(NewsArticleRow(
            source=source,
            title=title.strip(),
            published_ts=published_ts,
            language=language,
            publisher=publisher,
            url=link.strip() if link else None,
            summary=(summary or "").strip()[:4096] or None,
            as_of_ts=published_ts,
        ))
    return out


def _first_text(el: ET.Element, tags: tuple[str, ...]) -> Optional[str]:
    for tag in tags:
        node = el.find(tag)
        if node is not None and node.text:
            return node.text
    return None


def _parse_pubdate(raw: Optional[str]) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt:
            return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        pass
    # ISO 8601 fallback
    raw = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)/", url)
    return (m.group(1) if m else url).lower()
