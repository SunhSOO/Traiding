"""Module performance attribution endpoint.

``GET /summary?days=180&market=KR``

Pairs every closed PaperTrade with the DecisionAudit row that
spawned it (via ``decision_audit_id``), reads the F/T/I scores from
the audit row, computes forward return = trade.pnl / (entry_value),
and feeds those (score, return) pairs into ``analytics.attribution``.

Output is per-module pearson r + sign accuracy + share-of-explained-
predictability."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.attribution import (
    AttributionSample, compute_attribution,
)
from core.db import get_async_db
from core.models.audit import DecisionAudit
from core.models.paper import PaperTrade
from core.security import CurrentUser
from core.types import Market

router = APIRouter()


class ModuleAttrOut(BaseModel):
    module: str
    n_samples: int
    mean_score: float
    mean_return: float
    pearson_r: Optional[float]
    sign_accuracy: Optional[float]
    attribution_share: float


class AttributionSummary(BaseModel):
    window_days: int
    market: Optional[str]
    total_samples: int
    modules: list[ModuleAttrOut]


@router.get("/summary", response_model=AttributionSummary)
async def attribution_summary(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    days: int = Query(180, ge=7, le=730),
    market: Optional[str] = Query(None),
    account_name: Optional[str] = Query(None, description="Restrict to one paper account; omit → aggregate all"),
) -> AttributionSummary:
    market_value: Optional[str] = None
    if market is not None:
        try:
            market_value = Market(market.upper()).value
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {market}") from e

    # Resolve account_id when an explicit name is given. Aggregate across
    # all accounts when not — attribution is dimensionless (Pearson r on
    # forward return fractions) so mixing currencies is fine for the
    # statistical question being asked.
    account_id: Optional[int] = None
    if account_name:
        from core.models.paper import PaperAccount
        from sqlalchemy import select as _sel
        acc_row = (await db.execute(
            _sel(PaperAccount).where(PaperAccount.name == account_name)
        )).scalars().first()
        if acc_row is None:
            raise HTTPException(404, f"unknown paper account: {account_name}")
        account_id = acc_row.id

    since = datetime.now(UTC) - timedelta(days=days)

    # Pull closed trades + decision audit IDs in the window
    trade_stmt = (
        select(
            PaperTrade.decision_audit_id,
            PaperTrade.entry_price, PaperTrade.exit_price,
            PaperTrade.volume, PaperTrade.pnl,
            PaperTrade.market, PaperTrade.ticker,
        )
        .where(and_(
            PaperTrade.exit_ts >= since,
            PaperTrade.decision_audit_id.is_not(None),
        ))
    )
    if market_value:
        trade_stmt = trade_stmt.where(PaperTrade.market == market_value)
    if account_id is not None:
        trade_stmt = trade_stmt.where(PaperTrade.account_id == account_id)
    trade_rows = list((await db.execute(trade_stmt)).all())

    # Bulk-load matching audit rows
    audit_ids = []
    for r in trade_rows:
        try:
            audit_ids.append(uuid.UUID(r[0]) if isinstance(r[0], str) else r[0])
        except (TypeError, ValueError):
            continue
    if not audit_ids:
        return AttributionSummary(
            window_days=days, market=market_value,
            total_samples=0, modules=[],
        )

    audit_rows = list((await db.execute(
        select(
            DecisionAudit.id,
            DecisionAudit.fundamental_score,
            DecisionAudit.technical_score,
            DecisionAudit.information_score,
        ).where(DecisionAudit.id.in_(audit_ids))
    )).all())
    by_audit = {row[0]: row for row in audit_rows}

    # Build samples
    samples: list[AttributionSample] = []
    for r in trade_rows:
        audit_id_raw, entry_price, exit_price, volume, pnl, mkt, tkr = r
        try:
            aid = uuid.UUID(audit_id_raw) if isinstance(audit_id_raw, str) else audit_id_raw
        except (TypeError, ValueError):
            continue
        audit = by_audit.get(aid)
        if audit is None:
            continue
        _, f, t, i = audit
        gross = float(entry_price) * float(volume)
        if gross <= 0:
            continue
        forward_return = float(pnl) / gross
        scores = {}
        if f is not None: scores["F"] = float(f)
        if t is not None: scores["T"] = float(t)
        if i is not None: scores["I"] = float(i)
        if not scores:
            continue
        samples.append(AttributionSample(
            module_scores=scores, forward_return=forward_return,
        ))

    result = compute_attribution(samples)
    return AttributionSummary(
        window_days=days,
        market=market_value,
        total_samples=result.n_samples_total,
        modules=[
            ModuleAttrOut(
                module=m.module,
                n_samples=m.n_samples,
                mean_score=m.mean_score,
                mean_return=m.mean_return,
                pearson_r=m.pearson_r,
                sign_accuracy=m.sign_accuracy,
                attribution_share=m.attribution_share,
            )
            for m in result.modules
        ],
    )
