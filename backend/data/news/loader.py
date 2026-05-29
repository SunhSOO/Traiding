"""News loader — combine adapters → dedup → map tickers → upsert.

`sync_news` orchestrates one batch:

1. Run each enabled adapter once (or N times if it returns per-ticker).
2. Dedup the combined stream by URL → fallback dedup_key.
3. Build a NameIndex from `securities` and map each article to tickers.
4. Bulk INSERT articles with ON CONFLICT DO NOTHING on URL / dedup_key.
5. Bulk INSERT mentions with ON CONFLICT DO NOTHING on the
   (article, market, ticker) PK.
6. Stamp `data_freshness` per source.

Each adapter is plugged via the `NewsSourceSpec` registry so a test
can run with three mocked adapters and one real one. The default
production set lives at the bottom of this module.

Body fetching is intentionally NOT done here — see the module note in
`news.py`. The info module fetches bodies lazily.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Optional

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.models.audit import DataFreshness
from core.models.news import NewsArticle, NewsTickerMention
from core.models.universe import Security
from core.types import Market
from data.news.ticker_mapper import NameIndex, map_article
from data.news.types import NewsArticleRow

log = get_logger(__name__)


SourceFetcher = Callable[[], list[NewsArticleRow]]


@dataclass
class NewsSourceSpec:
    name: str                  # value persisted into news_articles.source
    fetcher: SourceFetcher
    enabled: bool = True


@dataclass
class NewsSyncReport:
    sources_processed: int = 0
    sources_failed: int = 0
    articles_seen: int = 0
    articles_inserted: int = 0
    articles_skipped_dedup: int = 0
    mentions_inserted: int = 0
    errors: list[str] = field(default_factory=list)


def sync_news(
    session: Session,
    *,
    sources: list[NewsSourceSpec],
    rebuild_name_index_each_call: bool = True,
    chunk_size: int = 500,
) -> NewsSyncReport:
    """Run all enabled sources, dedup, map, upsert."""
    report = NewsSyncReport()
    now = datetime.now(UTC)

    # 1. Pull every enabled adapter
    combined: list[NewsArticleRow] = []
    for spec in sources:
        if not spec.enabled:
            continue
        try:
            rows = spec.fetcher()
        except Exception as e:
            log.exception("news.source_failed", source=spec.name)
            report.sources_failed += 1
            report.errors.append(f"{spec.name}: {e}")
            _stamp(session, spec.name, "news", now, ok=False, error=str(e))
            continue
        report.sources_processed += 1
        combined.extend(rows)
        _stamp(session, spec.name, "news", now, ok=True, rows=len(rows))
    report.articles_seen = len(combined)

    if not combined:
        return report

    # 2. In-batch dedup: URL first, dedup_key second. Newer published_ts wins.
    deduped: dict[str, NewsArticleRow] = {}
    for row in combined:
        key = row.url or row.dedup_key
        prev = deduped.get(key)
        if prev is None or row.published_ts > prev.published_ts:
            deduped[key] = row
    rows = list(deduped.values())
    report.articles_skipped_dedup += len(combined) - len(rows)

    # 3. NameIndex once
    name_index = _build_name_index(session) if rebuild_name_index_each_call else _NAME_INDEX_CACHE.get_or_build(session)

    # 4. Upsert articles + mentions in chunks
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        inserted_articles, mentions_rows = _upsert_chunk(session, chunk, name_index)
        report.articles_inserted += inserted_articles
        report.mentions_inserted += _upsert_mentions(session, mentions_rows)

    log.info(
        "news.sync_done",
        sources_processed=report.sources_processed, sources_failed=report.sources_failed,
        seen=report.articles_seen, inserted=report.articles_inserted,
        deduped=report.articles_skipped_dedup, mentions=report.mentions_inserted,
    )
    return report


# ──────────────────────────────────────────────────────────────────────


def _upsert_chunk(
    session: Session,
    chunk: list[NewsArticleRow],
    name_index: NameIndex,
) -> tuple[int, list[dict]]:
    """Insert chunk articles; return (inserted_count, mention_rows_for_inserted)."""
    payload = []
    article_id_by_key: dict[str, uuid.UUID] = {}
    for row in chunk:
        aid = uuid.uuid4()
        payload.append({
            "id": aid,
            "published_ts": row.published_ts,
            "source": row.source,
            "publisher": row.publisher,
            "url": row.url,
            "dedup_key": row.dedup_key,
            "language": row.language,
            "title": row.title[:1024],
            "summary": row.summary[:4096] if row.summary else None,
            "body_text": row.body_text,
            "body_fetched": 1 if row.body_text else 0,
            "as_of_ts": row.as_of_ts or row.published_ts,
        })
        article_id_by_key[row.url or row.dedup_key] = aid

    # Pre-dedup against existing URLs in the DB. ON CONFLICT can only
    # target ONE constraint at a time and we have two (uq_*_url and
    # uq_*_dedup). Dropping URL-duplicates client-side lets the
    # statement's ON CONFLICT handle the dedup_key side cleanly.
    urls = [r.url for r in chunk if r.url]
    if urls:
        existing_urls = set(session.scalars(
            select(NewsArticle.url).where(NewsArticle.url.in_(urls))
        ).all())
        if existing_urls:
            payload = [p for p in payload if p["url"] not in existing_urls]
    if not payload:
        inserted_count = 0
    else:
        stmt = (
            pg_insert(NewsArticle)
            .values(payload)
            .on_conflict_do_nothing(constraint="uq_news_articles_dedup")
        )
        result = session.execute(stmt)
        session.flush()
        inserted_count = result.rowcount if result.rowcount is not None and result.rowcount >= 0 else 0

    # We need the IDs of articles that actually landed (URL collisions
    # mean some chunk entries already exist with different IDs). Re-read
    # those rows by dedup_key to get the surviving article_id.
    dedup_keys = [r.dedup_key for r in chunk]
    rows_in_db = session.scalars(
        select(NewsArticle).where(NewsArticle.dedup_key.in_(dedup_keys))
    )
    id_by_dedup = {row.dedup_key: row.id for row in rows_in_db}

    # Build mention rows for every article in the chunk that has a DB id
    mentions: list[dict] = []
    for row in chunk:
        aid = id_by_dedup.get(row.dedup_key)
        if aid is None:
            continue
        ticker_mentions = map_article(
            title=row.title, summary=row.summary, source_tags=(), index=name_index,
        )
        for tm in ticker_mentions:
            mentions.append({
                "article_id": aid,
                "article_published_ts": row.published_ts,
                "market": tm.market.value,
                "ticker": tm.ticker,
                "mention_kind": tm.mention_kind,
                "relevance": tm.relevance,
            })

    return (inserted_count, mentions)


def _upsert_mentions(session: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = (
        pg_insert(NewsTickerMention)
        .values(rows)
        .on_conflict_do_nothing(constraint="pk_news_ticker_mentions")
    )
    result = session.execute(stmt)
    session.flush()
    return result.rowcount if result.rowcount is not None and result.rowcount >= 0 else 0


def _build_name_index(session: Session) -> NameIndex:
    kr_pairs = [
        (row.name, row.ticker)
        for row in session.scalars(
            select(Security).where(Security.market == "KR", Security.is_active.is_(True))
        )
    ]
    us_pairs = [
        (row.name, row.ticker)
        for row in session.scalars(
            select(Security).where(Security.market == "US", Security.is_active.is_(True))
        )
    ]
    return NameIndex.from_pairs(kr=kr_pairs, us=us_pairs)


class _NameIndexCache:
    """Optional 1-hour cache so back-to-back loader runs don't re-query
    the securities table."""

    def __init__(self):
        self._cached: NameIndex | None = None
        self._cached_at: datetime | None = None

    def get_or_build(self, session: Session) -> NameIndex:
        now = datetime.now(UTC)
        if self._cached is not None and self._cached_at is not None:
            if (now - self._cached_at).total_seconds() < 3600:
                return self._cached
        self._cached = _build_name_index(session)
        self._cached_at = now
        return self._cached

    def invalidate(self) -> None:
        self._cached = None
        self._cached_at = None


_NAME_INDEX_CACHE = _NameIndexCache()


def _stamp(
    session: Session, source: str, scope: str, now: datetime,
    *, ok: bool, rows: Optional[int] = None, error: Optional[str] = None,
) -> None:
    stmt = select(DataFreshness).where(
        and_(DataFreshness.source == source, DataFreshness.market.is_(None),
             DataFreshness.scope == scope)
    )
    existing = session.scalars(stmt).first()
    if existing is None:
        existing = DataFreshness(source=source, market=None, scope=scope)
        session.add(existing)
    existing.last_attempt_ts = now
    if ok:
        existing.last_success_ts = now
        existing.last_error = None
        existing.rows_last_run = rows
    else:
        existing.last_error = error
    session.flush()
