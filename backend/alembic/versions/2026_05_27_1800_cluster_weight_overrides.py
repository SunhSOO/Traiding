"""cluster weight overrides

Adds ``cluster_weight_overrides`` — operator-controlled per-cluster
weight triples that override the values in ``cluster_weights``.

Revision ID: 0009_overrides
Revises: 0008_backfill
Create Date: 2026-05-27 18:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_overrides"
down_revision: Union[str, None] = "0008_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cluster_weight_overrides",
        sa.Column("cluster_id", sa.String(length=64), primary_key=True),
        sa.Column("w_fundamental", sa.Numeric(6, 5), nullable=False),
        sa.Column("w_technical", sa.Numeric(6, 5), nullable=False),
        sa.Column("w_information", sa.Numeric(6, 5), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("set_by", sa.String(length=64), nullable=False),
        sa.Column("set_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index(
        "ix_cluster_weight_overrides_set_ts",
        "cluster_weight_overrides", ["set_ts"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cluster_weight_overrides_set_ts",
        table_name="cluster_weight_overrides",
    )
    op.drop_table("cluster_weight_overrides")
