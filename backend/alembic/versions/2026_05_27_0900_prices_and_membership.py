"""prices and membership

Phase 1.1+1.2 — adds `daily_prices` (TimescaleDB hypertable),
`universe_membership` (index-constituent history), and `macro_series`
(generic time series for FX/rates/indices).

Revision ID: 0002_prices
Revises: 0001_initial
Create Date: 2026-05-27 09:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_prices"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── daily_prices ──
    op.create_table(
        "daily_prices",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("volume", sa.Numeric(precision=20, scale=0), nullable=False),
        sa.Column("adj_close", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("foreign_net", sa.Numeric(precision=20, scale=0), nullable=True),
        sa.Column("institution_net", sa.Numeric(precision=20, scale=0), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("as_of_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("trade_date", "market", "ticker", name="pk_daily_prices"),
    )
    op.create_index(
        "ix_daily_prices_ticker_date", "daily_prices",
        ["market", "ticker", "trade_date"],
    )
    # Convert to TimescaleDB hypertable partitioned on trade_date.
    # Chunk interval 90 days = ~quarterly partitions; reasonable for
    # 10y of ~870 tickers (~2M rows/year).
    from _migration_helpers import try_create_hypertable
    try_create_hypertable("daily_prices", "trade_date", chunk_interval="90 days")

    # ── universe_membership ──
    op.create_table(
        "universe_membership",
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("index_code", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("weight", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("as_of_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint(
            "market", "ticker", "index_code", "valid_from",
            name="pk_universe_membership",
        ),
    )
    op.create_index(
        "ix_universe_membership_index_active", "universe_membership",
        ["index_code", "valid_to"],
    )

    # ── macro_series ──
    op.create_table(
        "macro_series",
        sa.Column("series_code", sa.String(length=64), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("as_of_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("series_code", "ts", name="pk_macro_series"),
    )
    op.create_index("ix_macro_series_code", "macro_series", ["series_code"])
    try_create_hypertable("macro_series", "ts", chunk_interval="365 days")


def downgrade() -> None:
    op.drop_index("ix_macro_series_code", table_name="macro_series")
    op.drop_table("macro_series")
    op.drop_index("ix_universe_membership_index_active", table_name="universe_membership")
    op.drop_table("universe_membership")
    op.drop_index("ix_daily_prices_ticker_date", table_name="daily_prices")
    op.drop_table("daily_prices")
