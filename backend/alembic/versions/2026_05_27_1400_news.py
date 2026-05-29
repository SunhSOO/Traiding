"""news_articles + ticker mentions

Phase 1.6 — `news_articles` (TimescaleDB hypertable on published_ts)
and `news_ticker_mentions` (M:N bridge from articles to tickers).

Revision ID: 0004_news
Revises: 0003_fundamentals
Create Date: 2026-05-27 14:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0004_news"
down_revision: Union[str, None] = "0003_fundamentals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── news_articles ──
    op.create_table(
        "news_articles",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("published_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("publisher", sa.String(length=128), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("dedup_key", sa.String(length=40), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False, server_default="ko"),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_fetched", sa.Numeric(precision=1, scale=0), nullable=False, server_default="0"),
        sa.Column("as_of_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("published_ts", "id", name="pk_news_articles"),
        sa.UniqueConstraint("url", name="uq_news_articles_url"),
        sa.UniqueConstraint("dedup_key", name="uq_news_articles_dedup"),
    )
    op.create_index("ix_news_articles_publisher_ts", "news_articles", ["publisher", "published_ts"])
    op.create_index("ix_news_articles_source_ts", "news_articles", ["source", "published_ts"])
    op.create_index("ix_news_articles_language", "news_articles", ["language"])

    # 7-day chunks so a daily-news-volume table doesn't create a chunk per day.
    from _migration_helpers import try_create_hypertable
    try_create_hypertable("news_articles", "published_ts", chunk_interval="7 days")

    # ── news_ticker_mentions ──
    op.create_table(
        "news_ticker_mentions",
        sa.Column("article_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("article_published_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("mention_kind", sa.String(length=8), nullable=False),
        sa.Column("relevance", sa.Numeric(precision=4, scale=3), nullable=False, server_default="0.500"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint(
            "article_published_ts", "article_id", "market", "ticker",
            name="pk_news_ticker_mentions",
        ),
    )
    op.create_index("ix_nt_mentions_ticker_ts", "news_ticker_mentions",
                    ["market", "ticker", "article_published_ts"])
    op.create_index("ix_nt_mentions_relevance", "news_ticker_mentions",
                    ["market", "ticker", "relevance"])
    try_create_hypertable("news_ticker_mentions", "article_published_ts", chunk_interval="7 days")


def downgrade() -> None:
    op.drop_index("ix_nt_mentions_relevance", table_name="news_ticker_mentions")
    op.drop_index("ix_nt_mentions_ticker_ts", table_name="news_ticker_mentions")
    op.drop_table("news_ticker_mentions")
    op.drop_index("ix_news_articles_language", table_name="news_articles")
    op.drop_index("ix_news_articles_source_ts", table_name="news_articles")
    op.drop_index("ix_news_articles_publisher_ts", table_name="news_articles")
    op.drop_table("news_articles")
