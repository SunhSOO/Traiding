"""Persistence layer for trained cluster weights + ticker assignments.

Two read/write pairs:

- ``persist_assignments`` / ``load_current_assignments`` for ticker→cluster
- ``persist_cluster_weights`` / ``load_latest_weights`` for the OLS results

The decision engine consumes :func:`load_latest_weights` at runtime
to look up (w_F, w_T, w_I) per cluster. Writes are append-only;
old weights stay queryable for audit and walk-forward backtests."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable, Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.models.training import ClusterWeights, TickerClusterAssignment
from training.types import ClusterAssignment, TrainResult

log = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Assignments
# ──────────────────────────────────────────────────────────────────────


def persist_assignments(
    session: Session, assignments: Iterable[ClusterAssignment], *,
    assigned_at: Optional[datetime] = None,
) -> int:
    assigned_at = assigned_at or datetime.now(UTC)
    rows = list(assignments)
    if not rows:
        return 0
    objs = [
        TickerClusterAssignment(
            market=a.market, ticker=a.ticker, cluster_id=a.cluster_id,
            assigned_at=assigned_at, features=a.features or None,
        )
        for a in rows
    ]
    session.add_all(objs)
    session.flush()
    log.info("training.assignments_persisted", count=len(objs))
    return len(objs)


def load_current_assignments(
    session: Session, *, market: Optional[str] = None,
) -> dict[tuple[str, str], str]:
    """Return ``{(market, ticker): cluster_id}`` for the most recent
    assignment per (market, ticker)."""
    stmt = select(TickerClusterAssignment).order_by(
        TickerClusterAssignment.market,
        TickerClusterAssignment.ticker,
        desc(TickerClusterAssignment.assigned_at),
    )
    if market:
        stmt = stmt.where(TickerClusterAssignment.market == market)
    seen: dict[tuple[str, str], str] = {}
    for r in session.scalars(stmt):
        key = (r.market, r.ticker)
        if key not in seen:
            seen[key] = r.cluster_id
    return seen


# ──────────────────────────────────────────────────────────────────────
# Cluster weights
# ──────────────────────────────────────────────────────────────────────


def persist_cluster_weights(
    session: Session, results: Iterable[TrainResult], *,
    learned_at: Optional[datetime] = None,
) -> int:
    learned_at = learned_at or datetime.now(UTC)
    rows = list(results)
    if not rows:
        return 0
    objs = [
        ClusterWeights(
            cluster_id=r.cluster_id, learned_at=learned_at,
            w_fundamental=r.w_fundamental,
            w_technical=r.w_technical,
            w_information=r.w_information,
            intercept=r.intercept,
            n_samples=r.metrics.n_samples,
            n_tickers=r.metrics.n_tickers,
            model_version=r.model_version,
            metrics={
                "r2_in_sample": r.metrics.r2_in_sample,
                "r2_walk_forward": r.metrics.r2_walk_forward,
                "hit_rate": r.metrics.hit_rate,
            },
            notes=r.metrics.notes,
        )
        for r in rows
    ]
    session.add_all(objs)
    session.flush()
    log.info("training.cluster_weights_persisted", count=len(objs))
    return len(objs)


def load_latest_weights(
    session: Session,
) -> dict[str, dict[str, float]]:
    """Return ``{cluster_id: {F, T, I}}`` for the most recent training run."""
    stmt = (
        select(ClusterWeights)
        .order_by(ClusterWeights.cluster_id, desc(ClusterWeights.learned_at))
    )
    out: dict[str, dict[str, float]] = {}
    for r in session.scalars(stmt):
        if r.cluster_id in out:
            continue
        out[r.cluster_id] = {
            "F": float(r.w_fundamental),
            "T": float(r.w_technical),
            "I": float(r.w_information),
        }
    return out


def load_history(
    session: Session, cluster_id: str, *, limit: int = 20,
) -> list[ClusterWeights]:
    stmt = (
        select(ClusterWeights)
        .where(ClusterWeights.cluster_id == cluster_id)
        .order_by(desc(ClusterWeights.learned_at))
        .limit(limit)
    )
    return list(session.scalars(stmt))
