"""Risk dashboard endpoints.

Surfaces ``risk_snapshots`` aggregations:

- ``GET /summary`` — count of pass/fail per limit over a recent window
- ``GET /recent-failures`` — last N failed snapshots with their failure dicts

The decision runner already blocks REJECTED actions on risk-failure;
these endpoints exist so the operator can see *which* limits are
firing most often and tune limits / inputs accordingly."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.models.audit import RiskSnapshot
from core.security import CurrentUser
from core.types import Market

router = APIRouter()


class LimitStats(BaseModel):
    name: str
    pass_count: int
    fail_count: int
    total: int
    fail_rate: float


class RiskSummary(BaseModel):
    window_hours: int
    total_snapshots: int
    overall_pass_rate: float
    limits: list[LimitStats]


class RecentFailureOut(BaseModel):
    snapshot_ts: str
    market: str
    ticker: Optional[str]
    failed_limits: list[str]
    failures: Optional[dict] = None


LIMIT_COLUMNS = [
    ("max_lot",          "max_lot_pass"),
    ("daily_loss",       "daily_loss_pass"),
    ("consecutive_loss", "consecutive_loss_pass"),
    ("max_positions",    "max_positions_pass"),
    ("spread",           "spread_pass"),
    ("symbol_allowed",   "symbol_allowed_pass"),
]


@router.get("/summary", response_model=RiskSummary)
async def risk_summary(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    window_hours: int = Query(168, ge=1, le=2160, description="default: 7 days"),
    market: Optional[str] = Query(None, description="KR | US"),
) -> RiskSummary:
    since = datetime.now(UTC) - timedelta(hours=window_hours)

    base_filter = [RiskSnapshot.snapshot_ts >= since]
    if market:
        try:
            m = Market(market.upper())
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {market}") from e
        base_filter.append(RiskSnapshot.market == m.value)

    total = (await db.execute(
        select(func.count()).select_from(RiskSnapshot).where(and_(*base_filter))
    )).scalar() or 0
    if total == 0:
        return RiskSummary(
            window_hours=window_hours, total_snapshots=0,
            overall_pass_rate=0.0, limits=[],
        )

    overall_pass = (await db.execute(
        select(func.count()).select_from(RiskSnapshot).where(and_(
            *base_filter, RiskSnapshot.all_passed.is_(True),
        ))
    )).scalar() or 0
    overall_pass_rate = float(overall_pass) / float(total)

    limits: list[LimitStats] = []
    for name, col in LIMIT_COLUMNS:
        col_attr = getattr(RiskSnapshot, col)
        pass_count = (await db.execute(
            select(func.count()).select_from(RiskSnapshot).where(and_(
                *base_filter, col_attr.is_(True),
            ))
        )).scalar() or 0
        fail_count = total - int(pass_count)
        limits.append(LimitStats(
            name=name,
            pass_count=int(pass_count),
            fail_count=fail_count,
            total=int(total),
            fail_rate=fail_count / float(total) if total else 0.0,
        ))

    return RiskSummary(
        window_hours=window_hours,
        total_snapshots=int(total),
        overall_pass_rate=overall_pass_rate,
        limits=limits,
    )


@router.get("/recent-failures", response_model=list[RecentFailureOut])
async def recent_failures(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    limit: int = Query(50, ge=1, le=500),
    market: Optional[str] = Query(None, description="KR | US"),
) -> list[RecentFailureOut]:
    stmt = (
        select(RiskSnapshot)
        .where(RiskSnapshot.all_passed.is_(False))
        .order_by(desc(RiskSnapshot.snapshot_ts))
        .limit(limit)
    )
    if market:
        try:
            m = Market(market.upper())
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {market}") from e
        stmt = stmt.where(RiskSnapshot.market == m.value)

    out: list[RecentFailureOut] = []
    for r in (await db.execute(stmt)).scalars():
        failed = [
            name for name, col in LIMIT_COLUMNS
            if getattr(r, col) is False
        ]
        out.append(RecentFailureOut(
            snapshot_ts=r.snapshot_ts.isoformat(),
            market=r.market, ticker=r.ticker,
            failed_limits=failed, failures=r.failures,
        ))
    return out
