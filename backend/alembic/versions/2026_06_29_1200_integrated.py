"""integrated strategy tables

Adds `market_read` (daily market view + recommended exposure) and
`selection_basket` (alpha's intended holdings per market/date) for the
selection→execution pipeline.

Revision ID: 0012_integrated
Revises: 0011_regime
Create Date: 2026-06-29 12:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0012_integrated"
down_revision: Union[str, None] = "0011_regime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_read",
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("regime", sa.String(length=16), nullable=False),
        sa.Column("regime_conf", sa.Numeric(precision=5, scale=4), nullable=False, server_default="0"),
        sa.Column("breadth", sa.Numeric(precision=5, scale=4), nullable=False, server_default="0"),
        sa.Column("avg_conviction", sa.Numeric(precision=6, scale=4), nullable=False, server_default="0"),
        sa.Column("target_exposure", sa.Numeric(precision=5, scale=4), nullable=False, server_default="1"),
        sa.Column("inputs", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("market", "as_of", name="pk_market_read"),
    )
    op.create_table(
        "selection_basket",
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("rank_pct", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column("target_weight", sa.Numeric(precision=6, scale=5), nullable=False, server_default="0"),
        sa.Column("pred_ret_21d", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("target_price", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("band_low", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("band_high", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("in_basket", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("regime", sa.String(length=16), nullable=True),
        sa.Column("market_exposure", sa.Numeric(precision=5, scale=4), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("market", "as_of", "ticker", name="pk_selection_basket"),
    )
    op.create_index("ix_selection_basket_market_asof", "selection_basket", ["market", "as_of"])
    op.create_index("ix_selection_basket_inbasket", "selection_basket", ["market", "as_of", "in_basket"])


def downgrade() -> None:
    op.drop_index("ix_selection_basket_inbasket", table_name="selection_basket")
    op.drop_index("ix_selection_basket_market_asof", table_name="selection_basket")
    op.drop_table("selection_basket")
    op.drop_table("market_read")
