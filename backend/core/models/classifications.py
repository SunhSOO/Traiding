"""LLM classifications of news articles and disclosures.

This table is what makes the information module auditable: every
score the system ever produces traces back to a JSON blob here that
captures what the LLM said about a specific article.

Schema decisions:

- ``article_kind`` is 'news' | 'disclosure'. UUID alone wouldn't
  guarantee uniqueness across the two source tables, so we carry
  the kind as part of the PK.
- ``model_version`` is part of the PK so re-classifying the same
  article with a newer LLM creates a NEW row rather than overwriting
  the old one. Backtests use the model_version that was current at
  the time the score was computed.
- ``raw_llm_output`` keeps the full JSON the model produced for
  audit (and prompt-engineering forensics when a score looks off).
  Normalised fields (event_type/sentiment/...) are extracted into
  typed columns so queries don't need to dig into JSONB.
- No FK to news_articles / disclosures because they're hypertables
  on different partition keys; we'd need a composite FK including
  the partition column, which complicates writes. Instead we
  enforce referential integrity in the runner (article must exist
  in the source table or the row isn't inserted).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class ArticleClassification(Base):
    """One LLM classification of one article.

    `event_type`:
        EARNINGS, GUIDANCE, M_AND_A, REGULATORY, MANAGEMENT,
        PRODUCT, MACRO, INSIDER_TX, OTHER

    `sentiment`:
        POSITIVE, NEUTRAL, NEGATIVE

    `impact`:
        HIGH, MEDIUM, LOW   — magnitude of expected market reaction

    `horizon`:
        INTRADAY, SHORT_TERM (days–weeks), MEDIUM_TERM (months),
        LONG_TERM (quarters+)
    """

    __tablename__ = "article_classifications"
    __table_args__ = (
        PrimaryKeyConstraint(
            "article_kind", "article_id", "model_version",
            name="pk_article_classifications",
        ),
        Index("ix_article_class_classified_ts", "classified_ts"),
        Index("ix_article_class_event_ts", "event_type", "classified_ts"),
    )

    article_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, doc="news | disclosure"
    )
    article_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    model_version: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="Provider:model:revision — e.g. 'ollama:qwen2.5:14b'",
    )

    classified_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    sentiment: Mapped[str] = mapped_column(String(16), nullable=False)
    impact: Mapped[str] = mapped_column(String(8), nullable=False)
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False,
        doc="Model's self-reported confidence, 0..1",
    )

    summary: Mapped[Optional[str]] = mapped_column(
        Text, doc="Short LLM-generated summary (≤ 1 KB)"
    )
    raw_llm_output: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    error: Mapped[Optional[str]] = mapped_column(
        Text,
        doc="Set when classification failed; the row still lands so we "
            "don't re-attempt the same article in tight loops.",
    )
