"""Signal-scan endpoint — dry-run of the decision engine.

``GET /api/scan/preview?market=KR``

Returns one row per active ticker with the predicted action under
the current weights + module scores. The operator uses this before
the daily decision cron to see what's about to happen."""
from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.models.overrides import ClusterWeightOverride
from core.models.scores import ModuleScore
from core.models.training import ClusterWeights, TickerClusterAssignment
from core.models.universe import Security
from core.security import CurrentUser
from core.types import Market
from decision.types import DecisionConfig
from scan.engine import ScanRequest, TickerScoreSnapshot, scan_signals

router = APIRouter()


class ScanRow(BaseModel):
    market: str
    ticker: str
    name: Optional[str]
    cluster_id: Optional[str]
    composite_score: float
    composite_confidence: float
    action: str
    reason: str
    fundamental_score: Optional[float] = None
    technical_score: Optional[float] = None
    information_score: Optional[float] = None
    contributing_modules: list[str] = []
    weights_used: dict[str, float] = {}
    stalest_module: Optional[str] = None
    stalest_age_hours: Optional[float] = None


class ScanSummary(BaseModel):
    buy_count: int = 0
    sell_count: int = 0
    hold_count: int = 0
    no_signal_count: int = 0   # contributing modules empty


class ScanResponse(BaseModel):
    as_of: str
    market: Optional[str]
    summary: ScanSummary
    rows: list[ScanRow]


@router.get("/preview", response_model=ScanResponse)
async def preview(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None, description="KR | US (omit for both)"),
    buy_threshold: float = Query(25.0),
    sell_threshold: float = Query(-25.0),
    min_overall_confidence: float = Query(0.40, ge=0.0, le=1.0),
    use_learned_weights: bool = Query(True),
    action_filter: Optional[str] = Query(None, description="BUY | SELL | HOLD"),
    limit: int = Query(500, ge=1, le=5000),
) -> ScanResponse:
    market_value: Optional[str] = None
    if market is not None:
        try:
            market_value = Market(market.upper()).value
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {market}") from e

    as_of = datetime.now(UTC)

    # 1. Active securities
    sec_stmt = select(Security).where(Security.is_active.is_(True)).limit(limit)
    if market_value:
        sec_stmt = sec_stmt.where(Security.market == market_value)
    securities = list((await db.execute(sec_stmt)).scalars())
    if not securities:
        return ScanResponse(
            as_of=as_of.isoformat(), market=market_value,
            summary=ScanSummary(), rows=[],
        )

    keys = {(s.market, s.ticker) for s in securities}
    names = {(s.market, s.ticker): s.name for s in securities}

    # 2. Latest score per (market, ticker, module) — single query, dedup in memory
    score_stmt = (
        select(
            ModuleScore.market, ModuleScore.ticker, ModuleScore.module,
            ModuleScore.computed_ts, ModuleScore.score, ModuleScore.confidence,
        )
        .where(and_(
            ModuleScore.market.in_({m for m, _ in keys}),
            ModuleScore.ticker.in_({t for _, t in keys}),
        ))
        .order_by(desc(ModuleScore.computed_ts))
    )
    latest: dict[tuple[str, str, str], tuple] = {}
    for mkt, tkr, mod, ts, score, conf in (await db.execute(score_stmt)).all():
        key = (mkt, tkr, mod)
        if key in latest:
            continue
        latest[key] = (ts, float(score), float(conf))

    # 3. Ticker → cluster_id (latest)
    cluster_map: dict[tuple[str, str], str] = {}
    if use_learned_weights:
        rows = list((await db.execute(
            select(
                TickerClusterAssignment.market,
                TickerClusterAssignment.ticker,
                TickerClusterAssignment.cluster_id,
                TickerClusterAssignment.assigned_at,
            )
            .where(TickerClusterAssignment.market.in_({m for m, _ in keys}))
            .order_by(desc(TickerClusterAssignment.assigned_at))
        )).all())
        seen: set[tuple[str, str]] = set()
        for mkt, tkr, cid, _ts in rows:
            k = (mkt, tkr)
            if k in seen or k not in keys:
                continue
            seen.add(k)
            cluster_map[k] = cid

    # 4. Build DecisionConfig with learned weights + operator overrides
    config = DecisionConfig()
    if use_learned_weights:
        cluster_overrides: dict[str, dict[str, float]] = {}
        weights_rows = list((await db.execute(
            select(
                ClusterWeights.cluster_id,
                ClusterWeights.w_fundamental,
                ClusterWeights.w_technical,
                ClusterWeights.w_information,
                ClusterWeights.learned_at,
            ).order_by(desc(ClusterWeights.learned_at))
        )).all())
        for cid, wf, wt, wi, _ts in weights_rows:
            if cid not in cluster_overrides:
                cluster_overrides[cid] = {"F": float(wf), "T": float(wt), "I": float(wi)}

        # Operator overrides take precedence
        op_rows = list((await db.execute(select(ClusterWeightOverride))).scalars())
        for r in op_rows:
            cluster_overrides[r.cluster_id] = {
                "F": float(r.w_fundamental),
                "T": float(r.w_technical),
                "I": float(r.w_information),
            }
        config = dc_replace(config, cluster_weight_overrides=cluster_overrides)

    # 5. Build snapshots
    snapshots: list[TickerScoreSnapshot] = []
    for s in securities:
        f = latest.get((s.market, s.ticker, "F"))
        t = latest.get((s.market, s.ticker, "T"))
        i = latest.get((s.market, s.ticker, "I"))
        snapshots.append(TickerScoreSnapshot(
            market=s.market, ticker=s.ticker, name=names.get((s.market, s.ticker)),
            fundamental_ts=f[0] if f else None,
            fundamental_score=f[1] if f else None,
            fundamental_confidence=f[2] if f else None,
            technical_ts=t[0] if t else None,
            technical_score=t[1] if t else None,
            technical_confidence=t[2] if t else None,
            information_ts=i[0] if i else None,
            information_score=i[1] if i else None,
            information_confidence=i[2] if i else None,
            cluster_id=cluster_map.get((s.market, s.ticker)),
        ))

    # 6. Scan
    scan_request = ScanRequest(
        decision_config=config,
        as_of=as_of,
        buy_threshold=buy_threshold,
        sell_threshold=sell_threshold,
        min_overall_confidence=min_overall_confidence,
    )
    results = scan_signals(snapshots, request=scan_request)

    # 7. Summary
    summary = ScanSummary()
    for r in results:
        if not r.contributing_modules:
            summary.no_signal_count += 1
            continue
        if r.action.value == "BUY":
            summary.buy_count += 1
        elif r.action.value == "SELL":
            summary.sell_count += 1
        else:
            summary.hold_count += 1

    # 8. Filter + sort: BUY/SELL first (by |composite|), then HOLDs.
    if action_filter:
        action_filter = action_filter.upper()
        results = [r for r in results if r.action.value == action_filter]

    def sort_key(r):
        rank = 0 if r.action.value in ("BUY", "SELL") else 1
        return (rank, -abs(r.composite_score))
    results.sort(key=sort_key)

    rows = [
        ScanRow(
            market=r.market, ticker=r.ticker, name=r.name,
            cluster_id=r.cluster_id,
            composite_score=r.composite_score,
            composite_confidence=r.composite_confidence,
            action=r.action.value,
            reason=r.reason,
            fundamental_score=r.fundamental_score,
            technical_score=r.technical_score,
            information_score=r.information_score,
            contributing_modules=list(r.contributing_modules),
            weights_used=r.weights_used,
            stalest_module=r.stalest_module,
            stalest_age_hours=r.stalest_age_hours,
        )
        for r in results
    ]

    return ScanResponse(
        as_of=as_of.isoformat(), market=market_value,
        summary=summary, rows=rows,
    )
