"""Decision audit + risk snapshot persistence helpers.

Every order intent the system produces — whether it fires, gets
rejected by the risk engine, or is held — must result in exactly
one ``decision_audit`` row. The runtime/decision layer constructs
a :class:`DecisionRecord` and hands it to :func:`record_decision`,
which writes it (and the associated :class:`RiskSnapshot` from the
risk engine) in a single transaction.

We separate "build the record" from "persist the record" so the
in-memory builder can be unit-tested without a database, and the
persister can be integration-tested separately.

Read helpers (``get_decisions_for``, ``get_recent_decisions``) live
here too because they share the same table; they form the backbone
of the audit-trail UI in Phase 5.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from core.as_of import now as as_of_now
from core.logging import get_logger
from core.models.audit import DecisionAudit, RiskSnapshot
from core.risk import RiskCheckResult
from core.types import Market

log = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Record builders (pure)
# ──────────────────────────────────────────────────────────────────────


@dataclass
class ModuleScore:
    """One of the three analysis modules' verdicts for a single
    decision. ``score`` is in [-100, +100]; ``confidence`` in [0, 1].
    Either / both may be None if the module didn't run (e.g. not
    enough data yet)."""

    score: Optional[float] = None
    confidence: Optional[float] = None
    inputs: dict[str, Any] = field(default_factory=dict)
    """Raw inputs used by this module — preserved for audit. Keep
    small; for large blobs (LLM outputs, financials snapshots) use
    the dedicated llm_outputs / inputs_snapshot fields on DecisionRecord."""


@dataclass
class DecisionRecord:
    """In-memory representation of one decision before persistence.

    Construct one of these inside the decision engine, fill in what
    you know, then hand to :func:`record_decision`.
    """

    market: Market
    ticker: str
    action: str                                 # BUY | SELL | HOLD | REDUCE | EXIT | REJECTED
    decision_ts: Optional[datetime] = None      # defaults to as_of.now()

    fundamental: ModuleScore = field(default_factory=ModuleScore)
    technical: ModuleScore = field(default_factory=ModuleScore)
    information: ModuleScore = field(default_factory=ModuleScore)

    composite_score: Optional[float] = None
    composite_confidence: Optional[float] = None
    weights: dict[str, Any] = field(default_factory=dict)
    gate_results: dict[str, Any] = field(default_factory=dict)

    size_value: Optional[float] = None
    size_currency: Optional[str] = None
    model_version: Optional[str] = None
    corrects_id: Optional[uuid.UUID] = None

    inputs_snapshot: dict[str, Any] = field(default_factory=dict)
    llm_outputs: dict[str, Any] = field(default_factory=dict)
    execution_result: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    risk_snapshot_id: Optional[int] = None      # set after risk persistence

    def __post_init__(self) -> None:
        if self.decision_ts is None:
            object.__setattr__(self, "decision_ts", as_of_now())
        if self.action not in {"BUY", "SELL", "HOLD", "REDUCE", "EXIT", "REJECTED"}:
            raise ValueError(f"invalid action: {self.action!r}")
        # Score sanity (only when present)
        for name, m in (("fundamental", self.fundamental),
                        ("technical", self.technical),
                        ("information", self.information)):
            if m.score is not None and not (-100.0 <= m.score <= 100.0):
                raise ValueError(f"{name}.score {m.score} outside [-100, +100]")
            if m.confidence is not None and not (0.0 <= m.confidence <= 1.0):
                raise ValueError(f"{name}.confidence {m.confidence} outside [0, 1]")


# ──────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────


def record_risk_snapshot(session: Session, result: RiskCheckResult) -> RiskSnapshot:
    """Persist a risk check result. Returns the new row (with `id`)."""
    row = RiskSnapshot(
        snapshot_ts=datetime.now(UTC),
        market=result.intent.market.value,
        ticker=result.intent.ticker,
        max_lot_pass=result.max_lot_pass,
        max_lot_observed=float(result.intent.volume),
        max_lot_limit=float(result.limits.max_lot),
        daily_loss_pass=result.daily_loss_pass,
        daily_loss_observed=float(result.state.daily_pnl),
        daily_loss_limit=float(-result.limits.daily_loss_limit),
        consecutive_loss_pass=result.consecutive_loss_pass,
        consecutive_loss_observed=int(result.state.consecutive_losses),
        consecutive_loss_limit=int(result.limits.consecutive_loss_limit),
        max_positions_pass=result.max_positions_pass,
        max_positions_observed=int(result.state.open_position_count),
        max_positions_limit=int(result.limits.max_positions),
        spread_pass=result.spread_pass,
        spread_observed_bps=(
            float(result.state.current_spread_bps)
            if result.state.current_spread_bps is not None else None
        ),
        spread_limit_bps=float(result.limits.max_spread_bps),
        symbol_allowed_pass=result.symbol_allowed_pass,
        all_passed=result.all_passed,
        failures=[
            {
                "limit": f.limit_name,
                "observed": _jsonable(f.observed),
                "threshold": _jsonable(f.threshold),
                "reason": f.reason,
            }
            for f in result.failures
        ] or None,
    )
    session.add(row)
    session.flush()  # populate row.id without committing
    return row


def record_decision(session: Session, record: DecisionRecord) -> DecisionAudit:
    """Persist a decision audit row. Returns the new ORM row."""
    row = DecisionAudit(
        id=uuid.uuid4(),
        market=record.market.value,
        ticker=record.ticker,
        decision_ts=record.decision_ts or as_of_now(),
        fundamental_score=record.fundamental.score,
        fundamental_confidence=record.fundamental.confidence,
        technical_score=record.technical.score,
        technical_confidence=record.technical.confidence,
        information_score=record.information.score,
        information_confidence=record.information.confidence,
        composite_score=record.composite_score,
        composite_confidence=record.composite_confidence,
        action=record.action,
        size_value=record.size_value,
        size_currency=record.size_currency,
        risk_snapshot_id=record.risk_snapshot_id,
        model_version=record.model_version,
        corrects_id=record.corrects_id,
        weights=record.weights or None,
        gate_results=record.gate_results or None,
        inputs_snapshot=record.inputs_snapshot or None,
        llm_outputs=record.llm_outputs or None,
        execution_result=record.execution_result,
        error=record.error,
    )
    session.add(row)
    session.flush()
    log.info(
        "decision.recorded",
        market=record.market.value, ticker=record.ticker,
        action=record.action,
        composite=record.composite_score,
        decision_id=str(row.id),
    )
    return row


def record_decision_with_risk(
    session: Session,
    record: DecisionRecord,
    risk_result: RiskCheckResult,
) -> DecisionAudit:
    """Convenience: persist both, linking decision to risk snapshot.

    This is the path the runtime layer should use 99% of the time —
    every decision has a risk check, even if the action is HOLD
    (in which case the risk check is trivially passed but still
    recorded for completeness).
    """
    risk_row = record_risk_snapshot(session, risk_result)
    record.risk_snapshot_id = risk_row.id
    if not record.gate_results:
        record.gate_results = {
            "all_passed": risk_result.all_passed,
            "failed": [f.limit_name for f in risk_result.failures],
        }
    return record_decision(session, record)


# ──────────────────────────────────────────────────────────────────────
# Read helpers
# ──────────────────────────────────────────────────────────────────────


def get_decision(session: Session, decision_id: uuid.UUID) -> Optional[DecisionAudit]:
    return session.get(DecisionAudit, decision_id)


def get_recent_decisions(
    session: Session,
    *,
    market: Optional[Market] = None,
    ticker: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
) -> list[DecisionAudit]:
    stmt = select(DecisionAudit).order_by(desc(DecisionAudit.decision_ts)).limit(limit)
    if market is not None:
        stmt = stmt.where(DecisionAudit.market == market.value)
    if ticker is not None:
        stmt = stmt.where(DecisionAudit.ticker == ticker)
    if action is not None:
        stmt = stmt.where(DecisionAudit.action == action)
    return list(session.scalars(stmt))


def get_risk_snapshot(session: Session, snapshot_id: int) -> Optional[RiskSnapshot]:
    return session.get(RiskSnapshot, snapshot_id)


# ──────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────


def _jsonable(value: Any) -> Any:
    """Coerce values to JSON-serialisable forms so JSONB columns
    accept them. Used by the risk-failure payload builder."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Market):
        return value.value
    return str(value)
