"""Audit / meta tables.

The `decision_audit` table is the single source of truth for "why
did the system act on YYYY-MM-DD HH:MM:SS on ticker X". Every order
intent must produce one row here, regardless of whether it ultimately
fires (rejected by risk engine = still logged with `action='REJECTED'`).

Likewise `risk_snapshots` captures the full 6-limit risk check result
for every decision, so post-hoc audits can prove that risk gates ran.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAt, market_column


class DecisionAudit(Base):
    """Immutable record of every trading decision the system makes.

    A "decision" includes hold-but-evaluated cases — we want to know
    not only WHY we traded but also WHY WE DIDN'T (the composite
    score may have been below threshold, or a gate may have killed it).

    Append-only: rows are never updated after insert. Corrections go
    in as new rows referencing the original via `corrects_id`.
    """

    __tablename__ = "decision_audit"
    __table_args__ = (
        Index("ix_decision_audit_market_ticker_ts", "market", "ticker", "decision_ts"),
        Index("ix_decision_audit_action_ts", "action", "decision_ts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Subject
    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # When the decision was made (the operative `as_of`)
    decision_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Three module scores (-100 to +100 by convention)
    fundamental_score: Mapped[Optional[float]] = mapped_column(Numeric(6, 3))
    fundamental_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    technical_score: Mapped[Optional[float]] = mapped_column(Numeric(6, 3))
    technical_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    information_score: Mapped[Optional[float]] = mapped_column(Numeric(6, 3))
    information_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))

    composite_score: Mapped[Optional[float]] = mapped_column(Numeric(6, 3))
    composite_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))

    # Action taken
    action: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        doc="BUY | SELL | HOLD | REDUCE | EXIT | REJECTED",
    )
    size_value: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    size_currency: Mapped[Optional[str]] = mapped_column(String(3))

    # Linkage
    risk_snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("risk_snapshots.id"), index=True
    )
    model_version: Mapped[Optional[str]] = mapped_column(String(64))
    corrects_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("decision_audit.id")
    )

    # Bulk payloads
    weights: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    gate_results: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    inputs_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    llm_outputs: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    execution_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    error: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = CreatedAt


class RiskSnapshot(Base):
    """6-limit risk check result, captured BEFORE an order is sent.

    `all_passed=False` blocks the order but the snapshot is still
    persisted (and `DecisionAudit.action='REJECTED'`) so we can audit
    why the system refused to act.
    """

    __tablename__ = "risk_snapshots"
    __table_args__ = (
        Index("ix_risk_snapshots_market_ts", "market", "snapshot_ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    market: Mapped[str] = market_column()
    ticker: Mapped[Optional[str]] = mapped_column(String(16), index=True)

    # Each of the 6 limits: pass/fail flag + observed value + threshold
    max_lot_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    max_lot_observed: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    max_lot_limit: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))

    daily_loss_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    daily_loss_observed: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    daily_loss_limit: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))

    consecutive_loss_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consecutive_loss_observed: Mapped[Optional[int]] = mapped_column()
    consecutive_loss_limit: Mapped[Optional[int]] = mapped_column()

    max_positions_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    max_positions_observed: Mapped[Optional[int]] = mapped_column()
    max_positions_limit: Mapped[Optional[int]] = mapped_column()

    spread_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    spread_observed_bps: Mapped[Optional[float]] = mapped_column(Numeric(8, 3))
    spread_limit_bps: Mapped[Optional[float]] = mapped_column(Numeric(8, 3))

    symbol_allowed_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)

    all_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failures: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    created_at: Mapped[datetime] = CreatedAt


class DataFreshness(Base):
    """One row per (source, market, scope). Updated by every ingestion
    job, queried by the UI freshness dashboard and the alert engine."""

    __tablename__ = "data_freshness"
    __table_args__ = (
        UniqueConstraint("source", "market", "scope", name="uq_data_freshness_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc="dart | edgar | bigkinds | gdelt | pykrx | yfinance | fred | bok_ecos | wayback | naver | rss:<host>",
    )
    market: Mapped[Optional[str]] = mapped_column(String(8))
    scope: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="What this source provided: prices_daily | financials_q | news | filings | ...",
    )
    last_success_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_attempt_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    rows_last_run: Mapped[Optional[int]] = mapped_column()


class SecretMetadata(Base):
    """Catalog of which secrets the system EXPECTS to be configured.

    Holds NO secret values — only names and "where they're used"
    metadata. Useful for ops dashboards ("which keys haven't been
    validated in 30 days?") and for new operators ("what do I need
    to set up?").
    """

    __tablename__ = "secret_metadata"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required_for: Mapped[Optional[str]] = mapped_column(
        Text, doc="Which features this secret unlocks"
    )
    last_validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = CreatedAt
