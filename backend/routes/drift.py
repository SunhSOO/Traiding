"""Drift observation endpoints.

Two reads:

- ``GET /api/drift/score`` — per-ticker composite-score drift over
  recent decisions. Uses ``decision.drift.check_score_drift`` against
  each ticker's last N decisions. Surfaces tickers whose latest score
  is far from their own recent baseline.
- ``GET /api/drift/action-mix`` — today's BUY/SELL/HOLD/REJECTED
  distribution vs. the trailing 30-day distribution. Flags abrupt
  regime shifts.

Pure read-only. The decision engine doesn't act on drift today (it's
advisory); these endpoints surface what ``decision/drift.py`` already
computes so the operator can intervene before automation does."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.models.audit import DecisionAudit
from core.security import CurrentUser
from core.types import Market
from decision.drift import check_action_mix_drift, check_score_drift

router = APIRouter()


# ── Response shapes ──
class ScoreDriftRow(BaseModel):
    market: str
    ticker: str
    current_score: Optional[float]
    z_score: float
    mean: float
    stdev: float
    n_history: int
    is_anomaly: bool
    last_decision_ts: str


class ActionMixOut(BaseModel):
    today_distribution: dict[str, float]
    history_distribution: dict[str, float]
    total_variation_distance: float
    is_anomaly: bool
    today_count: int
    history_count: int


# ──────────────────────────────────────────────────────────────────────


@router.get("/score", response_model=list[ScoreDriftRow])
async def score_drift(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None, description="KR | US"),
    z_threshold: float = Query(3.0, ge=1.0, le=10.0),
    min_history: int = Query(5, ge=2, le=100),
    history_days: int = Query(30, ge=1, le=180),
) -> list[ScoreDriftRow]:
    """For each ticker with at least ``min_history`` decisions in the
    last ``history_days``, compute a z-score of the most recent
    composite vs. the prior decisions' distribution."""
    if market is not None:
        try:
            m = Market(market.upper())
        except ValueError:
            return []
        market_value = m.value
    else:
        market_value = None

    since = datetime.now(UTC) - timedelta(days=history_days)
    stmt = (
        select(
            DecisionAudit.market, DecisionAudit.ticker,
            DecisionAudit.composite_score, DecisionAudit.decision_ts,
        )
        .where(and_(
            DecisionAudit.decision_ts >= since,
            DecisionAudit.composite_score.is_not(None),
        ))
        .order_by(desc(DecisionAudit.decision_ts))
    )
    if market_value:
        stmt = stmt.where(DecisionAudit.market == market_value)

    # Group by (market, ticker), keep all scores; latest first
    by_key: dict[tuple[str, str], list[tuple[float, datetime]]] = {}
    for mkt, tkr, score, ts in (await db.execute(stmt)).all():
        if score is None:
            continue
        by_key.setdefault((mkt, tkr), []).append((float(score), ts))

    out: list[ScoreDriftRow] = []
    for (mkt, tkr), score_pairs in by_key.items():
        if len(score_pairs) < min_history + 1:
            # Need at least min_history baseline + 1 current
            continue
        current_score, last_ts = score_pairs[0]
        history = [s for s, _ in score_pairs[1 : 1 + 200]]  # cap history at 200
        check = check_score_drift(
            current_score=current_score, history=history,
            z_threshold=z_threshold, min_history=min_history,
        )
        out.append(ScoreDriftRow(
            market=mkt, ticker=tkr,
            current_score=current_score,
            z_score=check.zscore,
            mean=check.mean,
            stdev=check.stdev,
            n_history=check.n_history,
            is_anomaly=check.is_anomaly,
            last_decision_ts=last_ts.isoformat(),
        ))

    # Sort: anomalies first, then by |z| descending
    out.sort(key=lambda r: (not r.is_anomaly, -abs(r.z_score)))
    return out


@router.get("/action-mix", response_model=ActionMixOut)
async def action_mix(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None, description="KR | US"),
    today_window_hours: int = Query(24, ge=1, le=168),
    history_window_days: int = Query(30, ge=7, le=180),
    tv_threshold: float = Query(0.25, ge=0.0, le=1.0),
) -> ActionMixOut:
    """Compare the very-recent action distribution to the longer-term
    baseline. Returns total-variation distance (½ Σ |p_today − p_hist|).

    The decision engine doesn't block on this; surfaces only."""
    now = datetime.now(UTC)
    today_since = now - timedelta(hours=today_window_hours)
    history_since = now - timedelta(days=history_window_days)

    base_stmt = select(DecisionAudit.action, DecisionAudit.decision_ts)
    if market is not None:
        try:
            m = Market(market.upper())
        except ValueError:
            m = None
        if m is not None:
            base_stmt = base_stmt.where(DecisionAudit.market == m.value)

    # Today
    today_actions = [
        a for a, _ in (await db.execute(
            base_stmt.where(DecisionAudit.decision_ts >= today_since)
        )).all()
    ]
    # History — exclude the today window so the two are disjoint
    history_actions = [
        a for a, _ in (await db.execute(
            base_stmt.where(and_(
                DecisionAudit.decision_ts >= history_since,
                DecisionAudit.decision_ts < today_since,
            ))
        )).all()
    ]

    check = check_action_mix_drift(
        today_actions=today_actions,
        history_actions=history_actions,
        tv_threshold=tv_threshold,
        min_today=1,    # surface even small samples — the UI shows the count
    )
    return ActionMixOut(
        today_distribution=check.today_distribution,
        history_distribution=check.history_distribution,
        total_variation_distance=check.total_variation_distance,
        is_anomaly=check.is_anomaly,
        today_count=len(today_actions),
        history_count=len(history_actions),
    )
