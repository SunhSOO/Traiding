"""Universe endpoints — list securities and current index membership.

Read-only. Universe refresh itself is owned by the scheduler; the
ingestion router exposes a manual-trigger endpoint for ops.
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import desc, func

from core.db import get_async_db
from core.models.prices import UniverseMembership
from core.models.training import ClusterWeights, TickerClusterAssignment
from core.models.universe import Security
from core.security import CurrentUser
from core.types import Market

router = APIRouter()


# ── Schemas ──
class SecurityOut(BaseModel):
    market: str
    ticker: str
    name: str
    exchange: Optional[str]
    sector: Optional[str]
    industry: Optional[str]
    currency: str
    index_membership: Optional[str]
    is_active: bool
    # Cluster augmentation (best-effort — None if no cluster assigned)
    cluster_id: Optional[str] = None
    w_fundamental: Optional[float] = None
    w_technical: Optional[float] = None
    w_information: Optional[float] = None


class SecurityListOut(BaseModel):
    total: int
    rows: list[SecurityOut]


class UniverseFacets(BaseModel):
    sectors: list[str]
    indices: list[str]
    exchanges: list[str]


class MembershipOut(BaseModel):
    market: str
    ticker: str
    index_code: str
    valid_from: str
    valid_to: Optional[str]


# ── Routes ──
@router.get("/securities", response_model=SecurityListOut)
async def list_securities(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None, description="KR | US (omit for both)"),
    is_active: bool = Query(True, description="filter on Security.is_active"),
    search: Optional[str] = Query(None, description="name or ticker substring (case-insensitive)"),
    sector: Optional[str] = Query(None),
    index_code: Optional[str] = Query(None, description="KOSPI200 | KOSDAQ150 | SP500 | NASDAQ100"),
    exchange: Optional[str] = Query(None),
    cluster_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    with_cluster: bool = Query(True, description="join latest ticker→cluster assignment + weights"),
) -> SecurityListOut:
    base_filters = [Security.is_active.is_(is_active)]
    if market is not None:
        try:
            m = Market(market.upper())
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {market}") from e
        base_filters.append(Security.market == m.value)
    if sector:
        base_filters.append(Security.sector == sector)
    if exchange:
        base_filters.append(Security.exchange == exchange)
    if index_code:
        base_filters.append(Security.index_membership == index_code.upper())
    if search:
        like = f"%{search.lower()}%"
        base_filters.append(
            (func.lower(Security.name).like(like))
            | (func.lower(Security.ticker).like(like))
        )

    # Count for pagination
    total = (await db.execute(
        select(func.count()).select_from(Security).where(and_(*base_filters))
    )).scalar() or 0

    stmt = (
        select(Security)
        .where(and_(*base_filters))
        .order_by(Security.market, Security.ticker)
        .limit(limit).offset(offset)
    )
    securities = list((await db.execute(stmt)).scalars())

    # Cluster augmentation: latest assignment per ticker + latest weights per cluster.
    cluster_map: dict[tuple[str, str], str] = {}
    weights_by_cluster: dict[str, tuple[float, float, float]] = {}
    if with_cluster and securities:
        keys = {(s.market, s.ticker) for s in securities}
        assigns = list((await db.execute(
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
        for mkt, tkr, cid, _ts in assigns:
            key = (mkt, tkr)
            if key in seen or key not in keys:
                continue
            seen.add(key)
            cluster_map[key] = cid

        cluster_ids = set(cluster_map.values())
        if cluster_ids:
            weight_rows = list((await db.execute(
                select(
                    ClusterWeights.cluster_id,
                    ClusterWeights.w_fundamental,
                    ClusterWeights.w_technical,
                    ClusterWeights.w_information,
                    ClusterWeights.learned_at,
                )
                .where(ClusterWeights.cluster_id.in_(cluster_ids))
                .order_by(desc(ClusterWeights.learned_at))
            )).all())
            for cid, wf, wt, wi, _ts in weight_rows:
                if cid not in weights_by_cluster:
                    weights_by_cluster[cid] = (float(wf), float(wt), float(wi))

    if cluster_id:
        securities = [
            s for s in securities
            if cluster_map.get((s.market, s.ticker)) == cluster_id
        ]
        # Recompute total against the filtered list — can't filter in SQL
        # without a join, and this is rare enough that O(N) is fine.
        total = len(securities)

    rows: list[SecurityOut] = []
    for s in securities:
        key = (s.market, s.ticker)
        cid = cluster_map.get(key)
        w = weights_by_cluster.get(cid) if cid else None
        rows.append(SecurityOut(
            market=s.market, ticker=s.ticker, name=s.name,
            exchange=s.exchange, sector=s.sector, industry=s.industry,
            currency=s.currency, index_membership=s.index_membership,
            is_active=s.is_active,
            cluster_id=cid,
            w_fundamental=w[0] if w else None,
            w_technical=w[1] if w else None,
            w_information=w[2] if w else None,
        ))
    return SecurityListOut(total=int(total), rows=rows)


@router.get("/facets", response_model=UniverseFacets)
async def universe_facets(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None),
) -> UniverseFacets:
    """Distinct values for sector / index / exchange — used by the UI
    to populate filter dropdowns."""
    base_filters = [Security.is_active.is_(True)]
    if market is not None:
        try:
            m = Market(market.upper())
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {market}") from e
        base_filters.append(Security.market == m.value)

    sectors = [
        r[0] for r in (await db.execute(
            select(Security.sector).where(and_(*base_filters, Security.sector.is_not(None))).distinct()
        )).all() if r[0]
    ]
    indices = [
        r[0] for r in (await db.execute(
            select(Security.index_membership).where(and_(*base_filters, Security.index_membership.is_not(None))).distinct()
        )).all() if r[0]
    ]
    exchanges = [
        r[0] for r in (await db.execute(
            select(Security.exchange).where(and_(*base_filters, Security.exchange.is_not(None))).distinct()
        )).all() if r[0]
    ]
    return UniverseFacets(
        sectors=sorted(sectors), indices=sorted(indices), exchanges=sorted(exchanges),
    )


@router.get("/membership/{index_code}", response_model=list[MembershipOut])
async def list_membership(
    index_code: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    as_of: Optional[str] = Query(None, description="ISO date — defaults to today"),
) -> list[MembershipOut]:
    """Return tickers in the given index, optionally at a historical date.

    With no ``as_of``, returns currently-active members (``valid_to IS NULL``).
    With an ``as_of`` date, returns whoever was a member on that date.
    """
    from datetime import date as DateType, datetime

    if as_of is None:
        stmt = select(UniverseMembership).where(
            and_(
                UniverseMembership.index_code == index_code.upper(),
                UniverseMembership.valid_to.is_(None),
            )
        )
    else:
        try:
            d = datetime.strptime(as_of, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(400, "as_of must be YYYY-MM-DD") from e
        stmt = select(UniverseMembership).where(
            and_(
                UniverseMembership.index_code == index_code.upper(),
                UniverseMembership.valid_from <= d,
                (UniverseMembership.valid_to.is_(None)) | (UniverseMembership.valid_to > d),
            )
        )

    result = await db.execute(stmt.order_by(UniverseMembership.ticker))
    return [
        MembershipOut(
            market=r.market, ticker=r.ticker, index_code=r.index_code,
            valid_from=r.valid_from.isoformat(),
            valid_to=r.valid_to.isoformat() if r.valid_to else None,
        )
        for r in result.scalars()
    ]
