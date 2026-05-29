"""Backtest run persistence.

Each invocation of ``POST /api/backtest/run`` or
``POST /api/backtest/rescoring`` can be persisted here so the
operator can compare runs side-by-side later. The pure replay/
rescoring math doesn't need this — it's a UX layer.

Design choices:

- One row per run, no full trade-by-trade history. Trades and the
  equity curve are recomputable from ``config_snapshot`` if you ever
  need them again. Keeping the table thin keeps comparisons fast.

- ``mode`` distinguishes ``replay`` (past decisions as-is) from
  ``rescoring`` (current weights applied to past scores). The same
  table holds both so the comparison view doesn't have to UNION.

- ``label`` is operator-supplied for human-friendly tagging
  (``"weekly walk-forward 2026-05-25"``, ``"override F=0.5 sweep"``).
  Falls back to an auto-generated tag.

- All headline metrics are duplicated on the row so the comparison
  table can render without joining anywhere — drawdown / Sharpe / etc.

- ``triggered_by`` is either an operator username for manual runs or
  ``"scheduler:walk_forward"`` for the cron-driven path.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAt


class BacktestRunRow(Base):
    """One persisted backtest run."""

    __tablename__ = "backtest_runs"
    # Indices live on the migration side (op.create_index in
    # alembic/versions/2026_05_28_1500_backtest_runs.py) rather than
    # __table_args__ because the CreatedAt annotated-type pattern
    # interacts poorly with index column-name resolution during model
    # load. Keeping them migration-side has no runtime cost and
    # matches what alembic actually emits.

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="replay | rescoring",
    )
    market: Mapped[Optional[str]] = mapped_column(String(8))

    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    initial_balance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    final_equity: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    # Headline metrics — duplicated for cheap comparison rendering
    total_trades: Mapped[int] = mapped_column(nullable=False, default=0)
    win_rate: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    total_realized_pnl: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))
    avg_trade_pnl: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))
    best_trade_pnl: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))
    worst_trade_pnl: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))
    max_drawdown: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    sharpe_like: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    return_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    skipped_signals: Mapped[int] = mapped_column(nullable=False, default=0)

    # Full configuration + headline equity points for replay-vs-replay charts.
    # Trades themselves are NOT stored (recomputable from inputs).
    config_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    equity_points: Mapped[Optional[list[Any]]] = mapped_column(JSONB)

    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = CreatedAt
