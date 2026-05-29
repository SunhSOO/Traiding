"""module scores

Phase 2 — adds `module_scores` (TimescaleDB hypertable). Each row is
one analysis module's verdict on one ticker at one point in time.

Revision ID: 0005_scores
Revises: 0004_news
Create Date: 2026-05-28 09:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005_scores"
down_revision: Union[str, None] = "0004_news"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "module_scores",
        sa.Column("computed_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("module", sa.String(length=1), nullable=False),
        sa.Column("score", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("inputs", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint(
            "computed_ts", "market", "ticker", "module",
            name="pk_module_scores",
        ),
    )
    op.create_index("ix_module_scores_ticker_ts", "module_scores",
                    ["market", "ticker", "computed_ts"])
    op.create_index("ix_module_scores_module_ts", "module_scores",
                    ["module", "computed_ts"])
    from _migration_helpers import try_create_hypertable
    try_create_hypertable("module_scores", "computed_ts", chunk_interval="30 days")


def downgrade() -> None:
    op.drop_index("ix_module_scores_module_ts", table_name="module_scores")
    op.drop_index("ix_module_scores_ticker_ts", table_name="module_scores")
    op.drop_table("module_scores")
