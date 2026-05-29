"""Training visibility endpoints.

Three reads:

- ``GET /api/training/clusters`` — latest weights + metrics + ticker count for every
  cluster. Powers the operator's "is the model converging?" dashboard.
- ``GET /api/training/clusters/{cluster_id}/history`` — full weight history
  for one cluster, sortable by ``learned_at`` so the UI can draw
  weight-evolution sparklines.
- ``GET /api/training/summary`` — single-row snapshot of the last
  training run (when, how many clusters trained, total samples, etc).

All read-only. Triggering a re-train is done from the existing
``POST /api/ingestion/jobs/training.weekly/run`` endpoint.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.models.training import ClusterWeights, TickerClusterAssignment
from core.security import CurrentUser

router = APIRouter()


# ── Response shapes ──
class WeightRow(BaseModel):
    cluster_id: str
    learned_at: str
    w_fundamental: float
    w_technical: float
    w_information: float
    intercept: float
    n_samples: int
    n_tickers: int
    model_version: str
    metrics: Optional[dict[str, Any]] = None
    notes: Optional[str] = None


class ClusterSummary(BaseModel):
    cluster_id: str
    learned_at: str
    w_fundamental: float
    w_technical: float
    w_information: float
    n_samples: int
    n_tickers_in_run: int
    n_tickers_now: int            # current count from ticker_clusters
    r2_in_sample: Optional[float] = None
    r2_walk_forward: Optional[float] = None
    hit_rate: Optional[float] = None


class TrainingSummary(BaseModel):
    has_data: bool
    latest_learned_at: Optional[str] = None
    clusters_trained: int = 0
    total_samples: int = 0
    total_tickers_now: int = 0
    model_versions: list[str] = []


# ──────────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=TrainingSummary)
async def training_summary(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> TrainingSummary:
    # Latest learned_at across all clusters
    latest_ts = await db.scalar(select(func.max(ClusterWeights.learned_at)))
    if latest_ts is None:
        return TrainingSummary(has_data=False)

    # Latest row per cluster
    latest_rows = await _latest_per_cluster(db)
    total_samples = sum(int(r.n_samples) for r in latest_rows)
    model_versions = sorted({r.model_version for r in latest_rows})

    total_tickers_now = await db.scalar(
        select(func.count()).select_from(
            select(TickerClusterAssignment.market, TickerClusterAssignment.ticker)
            .distinct()
            .subquery()
        )
    ) or 0

    return TrainingSummary(
        has_data=True,
        latest_learned_at=latest_ts.isoformat() if latest_ts else None,
        clusters_trained=len(latest_rows),
        total_samples=total_samples,
        total_tickers_now=int(total_tickers_now),
        model_versions=model_versions,
    )


@router.get("/clusters", response_model=list[ClusterSummary])
async def list_clusters(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> list[ClusterSummary]:
    latest_rows = await _latest_per_cluster(db)
    if not latest_rows:
        return []

    # Current ticker counts per cluster (from ticker_clusters latest assignment)
    counts_now = await _current_ticker_counts(db)

    out: list[ClusterSummary] = []
    for r in latest_rows:
        metrics = r.metrics or {}
        out.append(ClusterSummary(
            cluster_id=r.cluster_id,
            learned_at=r.learned_at.isoformat(),
            w_fundamental=float(r.w_fundamental),
            w_technical=float(r.w_technical),
            w_information=float(r.w_information),
            n_samples=int(r.n_samples),
            n_tickers_in_run=int(r.n_tickers),
            n_tickers_now=counts_now.get(r.cluster_id, 0),
            r2_in_sample=_safe_float(metrics.get("r2_in_sample")),
            r2_walk_forward=_safe_float(metrics.get("r2_walk_forward")),
            hit_rate=_safe_float(metrics.get("hit_rate")),
        ))
    # Sort: by hit_rate desc (None last), then cluster_id
    out.sort(key=lambda x: (-(x.hit_rate or -1.0), x.cluster_id))
    return out


@router.get("/clusters/{cluster_id}/history", response_model=list[WeightRow])
async def cluster_history(
    cluster_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    limit: int = 50,
) -> list[WeightRow]:
    if not cluster_id:
        raise HTTPException(400, "cluster_id is required")
    stmt = (
        select(ClusterWeights)
        .where(ClusterWeights.cluster_id == cluster_id)
        .order_by(desc(ClusterWeights.learned_at))
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars())
    if not rows:
        raise HTTPException(404, f"no history for cluster {cluster_id!r}")
    return [
        WeightRow(
            cluster_id=r.cluster_id,
            learned_at=r.learned_at.isoformat(),
            w_fundamental=float(r.w_fundamental),
            w_technical=float(r.w_technical),
            w_information=float(r.w_information),
            intercept=float(r.intercept),
            n_samples=int(r.n_samples),
            n_tickers=int(r.n_tickers),
            model_version=r.model_version,
            metrics=r.metrics,
            notes=r.notes,
        )
        for r in rows
    ]


# ──────────────────────────────────────────────────────────────────────


async def _latest_per_cluster(db: AsyncSession) -> list[ClusterWeights]:
    """Return one row per cluster_id — the most recent training result."""
    subq = (
        select(
            ClusterWeights.cluster_id,
            func.max(ClusterWeights.learned_at).label("latest_ts"),
        )
        .group_by(ClusterWeights.cluster_id)
        .subquery()
    )
    stmt = (
        select(ClusterWeights)
        .join(subq, and_(
            ClusterWeights.cluster_id == subq.c.cluster_id,
            ClusterWeights.learned_at == subq.c.latest_ts,
        ))
    )
    return list((await db.execute(stmt)).scalars())


async def _current_ticker_counts(db: AsyncSession) -> dict[str, int]:
    """Count tickers per cluster using the LATEST assignment per ticker.

    Cheap-and-correct path: one query per (market, ticker) returning the
    latest assigned cluster_id, then aggregate in Python."""
    stmt = (
        select(
            TickerClusterAssignment.market,
            TickerClusterAssignment.ticker,
            TickerClusterAssignment.cluster_id,
            TickerClusterAssignment.assigned_at,
        )
        .order_by(desc(TickerClusterAssignment.assigned_at))
    )
    rows = list((await db.execute(stmt)).all())
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}
    for market, ticker, cluster_id, _ in rows:
        key = (market, ticker)
        if key in seen:
            continue
        seen.add(key)
        counts[cluster_id] = counts.get(cluster_id, 0) + 1
    return counts


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
