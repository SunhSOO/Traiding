"""backfill progress

Adds ``backfill_progress`` table: resumable per-(source × ticker × month)
checkpoint for the historical news backfill orchestrator.

Revision ID: 0008_backfill
Revises: 0007_training
Create Date: 2026-05-27 17:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_backfill"
down_revision: Union[str, None] = "0007_training"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backfill_progress",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("rows_inserted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("started_ts", sa.DateTime(timezone=True)),
        sa.Column("finished_ts", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint(
            "job_id", "source", "market", "ticker", "period",
            name="uq_backfill_chunk",
        ),
    )
    op.create_index(
        "ix_backfill_job_status", "backfill_progress",
        ["job_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_backfill_job_status", table_name="backfill_progress")
    op.drop_table("backfill_progress")
