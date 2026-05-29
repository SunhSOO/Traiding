"""financials and disclosures

Phase 1.3 + 1.5 — adds `financial_facts` (tall/skinny ticker × concept
× period) and `disclosures` (regulatory filing metadata + body).

Revision ID: 0003_fundamentals
Revises: 0002_prices
Create Date: 2026-05-27 11:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0003_fundamentals"
down_revision: Union[str, None] = "0002_prices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── financial_facts ──
    op.create_table(
        "financial_facts",
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("concept", sa.String(length=64), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("period_kind", sa.String(length=2), nullable=False),
        sa.Column("value", sa.Numeric(precision=24, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("unit_scale", sa.Numeric(precision=2, scale=0), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("raw_concept", sa.String(length=128), nullable=True),
        sa.Column("as_of_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint(
            "market", "ticker", "concept", "period_end", "period_kind",
            name="pk_financial_facts",
        ),
    )
    op.create_index(
        "ix_financial_facts_ticker_concept_period", "financial_facts",
        ["market", "ticker", "concept", "period_end"],
    )
    op.create_index("ix_financial_facts_period", "financial_facts", ["period_end"])

    # ── disclosures ──
    op.create_table(
        "disclosures",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("filing_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filing_type", sa.String(length=64), nullable=False),
        sa.Column("filing_type_canonical", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_fetched", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("amends_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("as_of_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_id", name="uq_disclosures_source"),
        sa.ForeignKeyConstraint(["amends_id"], ["disclosures.id"]),
    )
    op.create_index(
        "ix_disclosures_market_ticker_date", "disclosures",
        ["market", "ticker", "filing_date"],
    )
    op.create_index("ix_disclosures_filing_type", "disclosures", ["market", "filing_type"])
    op.create_index("ix_disclosures_market", "disclosures", ["market"])


def downgrade() -> None:
    op.drop_index("ix_disclosures_market", table_name="disclosures")
    op.drop_index("ix_disclosures_filing_type", table_name="disclosures")
    op.drop_index("ix_disclosures_market_ticker_date", table_name="disclosures")
    op.drop_table("disclosures")
    op.drop_index("ix_financial_facts_period", table_name="financial_facts")
    op.drop_index("ix_financial_facts_ticker_concept_period", table_name="financial_facts")
    op.drop_table("financial_facts")
