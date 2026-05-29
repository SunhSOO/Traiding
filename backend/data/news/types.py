"""Normalised news-article type returned by every adapter."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


_WHITESPACE_RE = re.compile(r"\s+")


def make_dedup_key(*, publisher: str | None, title: str, published_date: str) -> str:
    """Stable 40-char dedup hash for sources without a reliable URL.

    Normalises whitespace and case in the title so spelling-trivial
    variants collide (e.g. "Apple Inc"  vs "Apple  Inc.").
    """
    norm_title = _WHITESPACE_RE.sub(" ", (title or "").lower()).strip()
    payload = f"{(publisher or '').lower()}|{norm_title}|{published_date}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NewsArticleRow:
    """Adapter output. Loader does dedup + ticker mapping + upsert.

    `tags` are source-provided ticker tags (BIGKinds 종목 태그) —
    treated as authoritative ticker mentions with relevance=1.0.
    Empty for sources that don't tag (GDELT, RSS, Wayback).
    """

    source: str                    # 'bigkinds' | 'gdelt' | 'naver_search' | 'rss:<host>' | 'wayback'
    title: str
    published_ts: datetime
    language: str = "ko"

    publisher: Optional[str] = None
    url: Optional[str] = None
    summary: Optional[str] = None
    body_text: Optional[str] = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    as_of_ts: Optional[datetime] = None

    @property
    def dedup_key(self) -> str:
        return make_dedup_key(
            publisher=self.publisher,
            title=self.title,
            published_date=self.published_ts.date().isoformat(),
        )
