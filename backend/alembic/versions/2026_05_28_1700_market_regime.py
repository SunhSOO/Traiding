"""market regime

Adds ``market_regime`` table — one row per (market, ts) holding the
data-driven regime classifier output (label + confidence + voter
breakdown JSON).

Revision ID: 0011_regime
Revises: 0010_backtest
Create Date: 2026-05-28 17:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011_regime"
down_revision: Union[str, None] = "0010_backtest"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_regime",
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ts", sa.Date, nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("votes", JSONB, nullable=False),
        sa.Column("raw_inputs", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("market", "ts", name="pk_market_regime"),
    )


def downgrade() -> None:
    op.drop_table("market_regime")
