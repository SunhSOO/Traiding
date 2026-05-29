"""training tables

Phase 3 — adds `ticker_clusters` and `cluster_weights`.

Revision ID: 0007_training
Revises: 0006_classifications
Create Date: 2026-05-27 15:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007_training"
down_revision: Union[str, None] = "0006_classifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── ticker_clusters ──
    op.create_table(
        "ticker_clusters",
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("cluster_id", sa.String(length=64), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("features", JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("market", "ticker", "assigned_at", name="pk_ticker_clusters"),
    )
    op.create_index("ix_ticker_clusters_cluster", "ticker_clusters", ["cluster_id"])

    # ── cluster_weights ──
    op.create_table(
        "cluster_weights",
        sa.Column("cluster_id", sa.String(length=64), nullable=False),
        sa.Column("learned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("w_fundamental", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("w_technical", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("w_information", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("intercept", sa.Numeric(precision=10, scale=6), nullable=False, server_default="0"),
        sa.Column("n_samples", sa.Integer(), nullable=False),
        sa.Column("n_tickers", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("metrics", JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("cluster_id", "learned_at", name="pk_cluster_weights"),
    )
    op.create_index(
        "ix_cluster_weights_cluster_latest", "cluster_weights",
        ["cluster_id", "learned_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_cluster_weights_cluster_latest", table_name="cluster_weights")
    op.drop_table("cluster_weights")
    op.drop_index("ix_ticker_clusters_cluster", table_name="ticker_clusters")
    op.drop_table("ticker_clusters")
