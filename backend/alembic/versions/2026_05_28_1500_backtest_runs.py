"""backtest runs

Adds ``backtest_runs`` table. One row per persisted backtest
invocation (replay or re-scoring) so the operator can compare runs
side-by-side without re-executing.

Revision ID: 0010_backtest
Revises: 0009_overrides
Create Date: 2026-05-28 15:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

revision: str = "0010_backtest"
down_revision: Union[str, None] = "0009_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("market", sa.String(length=8)),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("initial_balance", sa.Numeric(18, 2), nullable=False),
        sa.Column("final_equity", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_trades", sa.Integer, nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Numeric(6, 4)),
        sa.Column("total_realized_pnl", sa.Numeric(18, 2)),
        sa.Column("avg_trade_pnl", sa.Numeric(18, 2)),
        sa.Column("best_trade_pnl", sa.Numeric(18, 2)),
        sa.Column("worst_trade_pnl", sa.Numeric(18, 2)),
        sa.Column("max_drawdown", sa.Numeric(6, 4)),
        sa.Column("sharpe_like", sa.Numeric(8, 4)),
        sa.Column("return_pct", sa.Numeric(8, 4)),
        sa.Column("skipped_signals", sa.Integer, nullable=False, server_default="0"),
        sa.Column("config_snapshot", JSONB),
        sa.Column("equity_points", JSONB),
        sa.Column("triggered_by", sa.String(length=64), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_backtest_runs_created_at", "backtest_runs", ["created_at"])
    op.create_index("ix_backtest_runs_mode_market", "backtest_runs", ["mode", "market"])
    op.create_index("ix_backtest_runs_label", "backtest_runs", ["label"])


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_label", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_mode_market", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_created_at", table_name="backtest_runs")
    op.drop_table("backtest_runs")
