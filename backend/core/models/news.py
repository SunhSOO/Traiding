"""News article + ticker-mention tables.

This is the largest table the system maintains by row count — every
article from BIGKinds/GDELT/RSS/Naver across years adds up fast.
``news_articles`` becomes a TimescaleDB hypertable partitioned on
``published_ts`` so window-based queries (the dominant pattern) stay
fast and old chunks can be dropped on retention policy if needed.

We **store summaries**, not full bodies, by default. Full body is
fetched lazily by the information-analysis module when it actually
wants to LLM-classify the article (Phase 2). This keeps disk
footprint manageable on a single PC (KOSPI200+KOSDAQ150+SP500+NDX
× ~3 articles/ticker/day × 5 years ≈ 4M rows; bodies would 10× that).

Dedup is by URL (unique). When multiple adapters return the same
article (BIGKinds + Naver both find a 매경 article), the first inserter
wins; subsequent inserts are ON CONFLICT DO NOTHING.

`news_ticker_mentions` is the bridge to securities. One article can
mention many tickers; one ticker can be in many articles. We rank
each (article, ticker) with a relevance score so the LLM and feature
layer can prefer high-signal mentions.
"""
from __future__ import annotations

import uuid
from datetime import date as DateType, datetime
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, market_column


class NewsArticle(Base):
    """One news article from any source.

    `id` is a UUID (we don't need monotonically-increasing IDs and
    UUIDs let us generate client-side before insert). `url` is the
    canonical dedup key.

    `dedup_key` is a fallback for sources that don't expose a stable
    URL (BIGKinds, Common Crawl extracts) — it's the SHA-1 of
    (publisher + normalized title + published_date) so the same
    article from different scrapes still collides.
    """

    __tablename__ = "news_articles"
    __table_args__ = (
        PrimaryKeyConstraint("published_ts", "id", name="pk_news_articles"),
        UniqueConstraint("url", name="uq_news_articles_url"),
        UniqueConstraint("dedup_key", name="uq_news_articles_dedup"),
        Index("ix_news_articles_publisher_ts", "publisher", "published_ts"),
        Index("ix_news_articles_source_ts", "source", "published_ts"),
        Index("ix_news_articles_language", "language"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), default=uuid.uuid4)
    published_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        doc="When the article was published (publisher's own timestamp). Hypertable partition key.",
    )

    source: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="bigkinds | gdelt | naver_search | rss:<host> | wayback",
    )
    publisher: Mapped[Optional[str]] = mapped_column(String(128))
    url: Mapped[Optional[str]] = mapped_column(String(2048))
    dedup_key: Mapped[str] = mapped_column(String(40), nullable=False)
    language: Mapped[str] = mapped_column(
        String(8), nullable=False, default="ko",
        doc="BCP-47 short — ko / en / ja / zh",
    )

    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(
        Text,
        doc="≤4 KB summary. NULL if source provides no summary and "
            "we haven't fetched the body.",
    )
    body_text: Mapped[Optional[str]] = mapped_column(Text)
    body_fetched: Mapped[bool] = mapped_column(
        Numeric(1, 0), nullable=False, default=0,
        doc="0 = body not yet fetched; 1 = full body persisted",
    )

    as_of_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        doc="When the article became known to us (published_ts + ingestion lag)",
    )
    ingested_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NewsTickerMention(Base):
    """Many-to-many bridge from articles to tickers, with relevance.

    `mention_kind`:
        'title'  — ticker name found in article title (strongest signal)
        'body'   — found in summary/body
        'tag'    — explicit tag from the source (BIGKinds 종목 태그)

    `relevance` in [0, 1]. Default scoring weights:
        title-only          → 0.9
        body-only           → 0.5
        title + body        → 0.95
        tag (any)           → 1.0

    Stored separately rather than computed at query time so the
    feature layer can index on (market, ticker, published_ts) for fast
    "all relevant news for ticker X in last N days" queries.
    """

    __tablename__ = "news_ticker_mentions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "article_published_ts", "article_id", "market", "ticker",
            name="pk_news_ticker_mentions",
        ),
        Index("ix_nt_mentions_ticker_ts", "market", "ticker", "article_published_ts"),
        Index("ix_nt_mentions_relevance", "market", "ticker", "relevance"),
    )

    article_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    article_published_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        doc="Carried from NewsArticle.published_ts so a single-table join is sufficient",
    )

    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)

    mention_kind: Mapped[str] = mapped_column(
        String(8), nullable=False, doc="title | body | tag"
    )
    relevance: Mapped[float] = mapped_column(
        Numeric(4, 3), nullable=False, default=0.5,
        doc="0..1 — see module docstring for default weights",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
