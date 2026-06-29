"""Integrated strategy read endpoints (selection→execution pipeline).

Serves the three new UX views designed for the new direction:
  - GET /api/integrated/market-read   시황: regime + breadth + conviction + exposure
  - GET /api/integrated/basket        선정: today's alpha basket (ranked, vs yesterday)
  - GET /api/integrated/execution     실행: per-name technical-timing status (BUY/WAIT/SELL)
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.models.audit import DecisionAudit
from core.models.integrated import MarketRead, SelectionBasket
from core.security import CurrentUser
from core.types import Market

router = APIRouter()


def _f(v) -> Optional[float]:
    return None if v is None else float(v)


# ── 시황 (Market read) ──────────────────────────────────────────────
class MarketReadOut(BaseModel):
    market: str
    as_of: str
    regime: str
    regime_conf: float
    breadth: float
    avg_conviction: float
    target_exposure: float
    n_universe: Optional[int] = None
    n_basket: Optional[int] = None


@router.get("/market-read", response_model=list[MarketReadOut])
async def market_read(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> list[MarketReadOut]:
    out: list[MarketReadOut] = []
    for market in (Market.KR, Market.US):
        row = (await db.execute(
            select(MarketRead).where(MarketRead.market == market.value)
            .order_by(desc(MarketRead.as_of)).limit(1)
        )).scalars().first()
        if row is None:
            continue
        inp = row.inputs or {}
        out.append(MarketReadOut(
            market=row.market, as_of=row.as_of.isoformat(), regime=row.regime,
            regime_conf=_f(row.regime_conf), breadth=_f(row.breadth),
            avg_conviction=_f(row.avg_conviction), target_exposure=_f(row.target_exposure),
            n_universe=inp.get("n_universe"), n_basket=inp.get("n_basket"),
        ))
    return out


# ── 선정 (Basket) ───────────────────────────────────────────────────
class BasketNameOut(BaseModel):
    ticker: str
    rank_pct: float
    target_weight: float
    pred_ret_21d: Optional[float]
    target_price: Optional[float]
    band_low: Optional[float]
    band_high: Optional[float]
    status: str               # "new" | "held" (vs previous basket)


class BasketOut(BaseModel):
    market: str
    as_of: str
    regime: Optional[str]
    target_exposure: float
    names: list[BasketNameOut]
    dropped: list[str]        # tickers in prev basket but not today


async def _basket_rows(db, market: str, as_of: date) -> list[SelectionBasket]:
    return list((await db.execute(
        select(SelectionBasket).where(
            SelectionBasket.market == market, SelectionBasket.as_of == as_of,
            SelectionBasket.in_basket.is_(True),
        ).order_by(desc(SelectionBasket.rank_pct))
    )).scalars().all())


@router.get("/basket", response_model=Optional[BasketOut])
async def basket(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: str = Query("KR"),
) -> Optional[BasketOut]:
    latest = (await db.execute(
        select(SelectionBasket.as_of).where(SelectionBasket.market == market)
        .order_by(desc(SelectionBasket.as_of)).limit(1)
    )).scalars().first()
    if latest is None:
        return None
    rows = await _basket_rows(db, market, latest)
    # previous basket for new/dropped diff
    prev_date = (await db.execute(
        select(SelectionBasket.as_of).where(
            SelectionBasket.market == market, SelectionBasket.as_of < latest)
        .order_by(desc(SelectionBasket.as_of)).limit(1)
    )).scalars().first()
    prev = set()
    if prev_date is not None:
        prev = {r.ticker for r in await _basket_rows(db, market, prev_date)}
    today = {r.ticker for r in rows}
    names = [BasketNameOut(
        ticker=r.ticker, rank_pct=_f(r.rank_pct), target_weight=_f(r.target_weight),
        pred_ret_21d=_f(r.pred_ret_21d), target_price=_f(r.target_price),
        band_low=_f(r.band_low), band_high=_f(r.band_high),
        status=("held" if r.ticker in prev else "new"),
    ) for r in rows]
    return BasketOut(
        market=market, as_of=latest.isoformat(),
        regime=(rows[0].regime if rows else None),
        target_exposure=(_f(rows[0].market_exposure) if rows else 1.0),
        names=names, dropped=sorted(prev - today),
    )


# ── 실행 (Execution timing) ─────────────────────────────────────────
class ExecRowOut(BaseModel):
    ticker: str
    action: str               # BUY | SELL | WAIT | REJECTED
    rank_pct: Optional[float]
    tech_score: Optional[float]
    timing: Optional[str]     # enter_ok | wait_tech | exit_* ...
    size_value: Optional[float]
    filled: Optional[bool]


class ExecutionOut(BaseModel):
    market: str
    as_of: Optional[str]
    rows: list[ExecRowOut]


@router.get("/execution", response_model=ExecutionOut)
async def execution(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: str = Query("KR"),
) -> ExecutionOut:
    # latest integrated decision batch
    latest_ts = (await db.execute(
        select(DecisionAudit.decision_ts).where(
            DecisionAudit.market == market,
            DecisionAudit.model_version == f"integrated_{market}_v1",
        ).order_by(desc(DecisionAudit.decision_ts)).limit(1)
    )).scalars().first()
    if latest_ts is None:
        return ExecutionOut(market=market, as_of=None, rows=[])
    rows = list((await db.execute(
        select(DecisionAudit).where(
            DecisionAudit.market == market,
            DecisionAudit.model_version == f"integrated_{market}_v1",
            DecisionAudit.decision_ts == latest_ts,
        )
    )).scalars().all())
    out_rows: list[ExecRowOut] = []
    for r in rows:
        snap = r.inputs_snapshot or {}
        sel = snap.get("stage_selection", {})
        tim = snap.get("stage_timing", {})
        exec_res = r.execution_result or {}
        out_rows.append(ExecRowOut(
            ticker=r.ticker, action=r.action,
            rank_pct=sel.get("rank_pct"), tech_score=tim.get("tech_score"),
            timing=tim.get("decision"), size_value=_f(r.size_value),
            filled=(exec_res.get("ok") if r.execution_result else None),
        ))
    # order: BUY, WAIT, SELL, REJECTED
    order = {"BUY": 0, "WAIT": 1, "SELL": 2, "REJECTED": 3}
    out_rows.sort(key=lambda x: (order.get(x.action, 9), -(x.rank_pct or 0)))
    return ExecutionOut(market=market, as_of=latest_ts.isoformat(), rows=out_rows)
