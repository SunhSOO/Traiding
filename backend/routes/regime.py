"""Market regime read endpoints.

- ``GET /api/regime/current`` — latest regime per market, voter
  breakdown for the audit panel.
- ``GET /api/regime/history?days=180`` — daily readings for the
  ribbon chart on the macro page.
- ``POST /api/regime/run`` — manual classifier invocation (idempotent;
  same as the daily scheduler job)."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db, session_scope
from core.logging import get_logger
from core.models.regime import MarketRegime
from core.security import CurrentUser
from core.types import Market

log = get_logger(__name__)
router = APIRouter()


class RegimeReadingOut(BaseModel):
    market: str
    ts: str                         # YYYY-MM-DD
    label: str                      # RISK_ON | NEUTRAL | RISK_OFF
    confidence: float
    votes: list[dict]
    raw_inputs: dict
    created_at: str


class RegimeHistoryPoint(BaseModel):
    ts: str
    label: str
    confidence: float


class RegimeHistoryOut(BaseModel):
    market: str
    days: list[RegimeHistoryPoint]


class RunResponse(BaseModel):
    markets_classified: int
    markets_failed: int
    errors: list[str]


# ──────────────────────────────────────────────────────────────────────


@router.get("/current", response_model=list[RegimeReadingOut])
async def current_regime(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> list[RegimeReadingOut]:
    """Latest regime row per market."""
    out: list[RegimeReadingOut] = []
    for market in (Market.KR, Market.US):
        row = (await db.execute(
            select(MarketRegime)
            .where(MarketRegime.market == market.value)
            .order_by(desc(MarketRegime.ts))
            .limit(1)
        )).scalars().first()
        if row is None:
            continue
        out.append(_to_reading_out(row))
    return out


@router.get("/history", response_model=RegimeHistoryOut)
async def regime_history(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: str = Query(..., description="KR | US"),
    days: int = Query(180, ge=7, le=1825),
) -> RegimeHistoryOut:
    try:
        m = Market(market.upper())
    except ValueError as e:
        raise HTTPException(400, f"unknown market: {market}") from e

    since = date.today() - timedelta(days=days)
    rows = list((await db.execute(
        select(MarketRegime.ts, MarketRegime.label, MarketRegime.confidence)
        .where(and_(
            MarketRegime.market == m.value,
            MarketRegime.ts >= since,
        ))
        .order_by(MarketRegime.ts)
    )).all())
    return RegimeHistoryOut(
        market=m.value,
        days=[
            RegimeHistoryPoint(
                ts=ts.isoformat(), label=label, confidence=float(conf),
            )
            for ts, label, conf in rows
        ],
    )


@router.post("/run", response_model=RunResponse)
async def run_now(
    user: CurrentUser,
    as_of: Optional[datetime] = None,
) -> RunResponse:
    """Re-run the regime classifier (idempotent). Uses sync session
    scope because the classifier reads macro_series via the loader
    which is synchronous."""
    from regime.runner import run_regime_daily

    with session_scope() as session:
        report = run_regime_daily(session, as_of=as_of)
    return RunResponse(
        markets_classified=report.markets_classified,
        markets_failed=report.markets_failed,
        errors=report.errors,
    )


def _to_reading_out(row: MarketRegime) -> RegimeReadingOut:
    return RegimeReadingOut(
        market=row.market,
        ts=row.ts.isoformat(),
        label=row.label,
        confidence=float(row.confidence),
        votes=list(row.votes or []),
        raw_inputs=dict(row.raw_inputs or {}),
        created_at=row.created_at.isoformat(),
    )
