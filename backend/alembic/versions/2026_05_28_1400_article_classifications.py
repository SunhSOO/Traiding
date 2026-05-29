"""article classifications

Phase 2.3 — adds `article_classifications` (versioned LLM output for
news + disclosures). Not a hypertable: row count = O(articles), much
smaller than the source tables, and we frequently want random-access
lookups by article_id.

Revision ID: 0006_classifications
Revises: 0005_scores
Create Date: 2026-05-28 14:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

revision: str = "0006_classifications"
down_revision: Union[str, None] = "0005_scores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "article_classifications",
        sa.Column("article_kind", sa.String(length=16), nullable=False),
        sa.Column("article_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("classified_ts", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("sentiment", sa.String(length=16), nullable=False),
        sa.Column("impact", sa.String(length=8), nullable=False),
        sa.Column("horizon", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("raw_llm_output", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint(
            "article_kind", "article_id", "model_version",
            name="pk_article_classifications",
        ),
    )
    op.create_index("ix_article_class_classified_ts", "article_classifications", ["classified_ts"])
    op.create_index("ix_article_class_event_ts", "article_classifications", ["event_type", "classified_ts"])


def downgrade() -> None:
    op.drop_index("ix_article_class_event_ts", table_name="article_classifications")
    op.drop_index("ix_article_class_classified_ts", table_name="article_classifications")
    op.drop_table("article_classifications")
