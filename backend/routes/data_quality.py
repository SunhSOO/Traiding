"""Data quality endpoints.

Two reads:

- ``GET /freshness-by-ticker`` — per-ticker freshness tier (FRESH /
  STALE / DEAD) based on last price date.
- ``GET /gaps`` — gap windows for one or more tickers in a window.

The expensive bit is fetching every (market, ticker, trade_date) row;
we let the operator scope by market + a hard limit.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.data_quality import (
    FRESHNESS_THRESHOLDS, find_gaps, is_weekday, score_freshness,
)
from core.db import get_async_db
from core.models.prices import DailyPrice
from core.models.universe import Security
from core.security import CurrentUser
from core.types import Market

router = APIRouter()


class FreshnessRow(BaseModel):
    market: str
    ticker: str
    name: Optional[str]
    last_price_date: Optional[str]
    days_since_last: Optional[int]
    tier: str            # FRESH | STALE | DEAD


class FreshnessSummary(BaseModel):
    fresh_count: int
    stale_count: int
    dead_count: int
    total: int
    threshold_stale_days: dict[str, int]   # per-market
    threshold_dead_days: dict[str, int]


class FreshnessResponse(BaseModel):
    summary: FreshnessSummary
    rows: list[FreshnessRow]


class GapWindowOut(BaseModel):
    market: str
    ticker: str
    start: str
    end: str
    days: int


class GapsResponse(BaseModel):
    market: Optional[str]
    window_start: str
    window_end: str
    total_tickers_checked: int
    tickers_with_gaps: int
    windows: list[GapWindowOut]


# ──────────────────────────────────────────────────────────────────────


@router.get("/freshness-by-ticker", response_model=FreshnessResponse)
async def freshness_by_ticker(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None),
    tier_filter: Optional[str] = Query(None, description="FRESH | STALE | DEAD"),
    limit: int = Query(1000, ge=1, le=10000),
) -> FreshnessResponse:
    market_value = _resolve_market(market)
    today = date.today()

    # Build the latest trade_date per (market, ticker) — one round trip.
    base_filters = [Security.is_active.is_(True)]
    if market_value:
        base_filters.append(Security.market == market_value)

    securities = list((await db.execute(
        select(Security.market, Security.ticker, Security.name).where(and_(*base_filters)).limit(limit)
    )).all())
    if not securities:
        return FreshnessResponse(
            summary=FreshnessSummary(
                fresh_count=0, stale_count=0, dead_count=0, total=0,
                threshold_stale_days={m: v[0] for m, v in FRESHNESS_THRESHOLDS.items()},
                threshold_dead_days={m: v[1] for m, v in FRESHNESS_THRESHOLDS.items()},
            ),
            rows=[],
        )

    last_dates = list((await db.execute(
        select(
            DailyPrice.market, DailyPrice.ticker,
            func.max(DailyPrice.trade_date).label("last_d"),
        )
        .where(and_(
            DailyPrice.market.in_({s.market for s in securities}),
            DailyPrice.ticker.in_({s.ticker for s in securities}),
        ))
        .group_by(DailyPrice.market, DailyPrice.ticker)
    )).all())
    by_key = {(m, t): d for m, t, d in last_dates}

    rows: list[FreshnessRow] = []
    summary = FreshnessSummary(
        fresh_count=0, stale_count=0, dead_count=0, total=0,
        threshold_stale_days={m: v[0] for m, v in FRESHNESS_THRESHOLDS.items()},
        threshold_dead_days={m: v[1] for m, v in FRESHNESS_THRESHOLDS.items()},
    )
    for s in securities:
        last_d = by_key.get((s.market, s.ticker))
        fs = score_freshness(
            market=s.market, ticker=s.ticker,
            last_price_date=last_d, today=today,
        )
        if tier_filter and fs.tier != tier_filter.upper():
            continue
        rows.append(FreshnessRow(
            market=s.market, ticker=s.ticker, name=s.name,
            last_price_date=fs.last_price_date.isoformat() if fs.last_price_date else None,
            days_since_last=fs.days_since_last,
            tier=fs.tier,
        ))
        summary.total += 1
        if fs.tier == "FRESH": summary.fresh_count += 1
        elif fs.tier == "STALE": summary.stale_count += 1
        else: summary.dead_count += 1

    # Sort: DEAD first, then STALE, then FRESH by oldest
    tier_rank = {"DEAD": 0, "STALE": 1, "FRESH": 2}
    rows.sort(key=lambda r: (tier_rank.get(r.tier, 3), -(r.days_since_last or 0)))
    return FreshnessResponse(summary=summary, rows=rows)


@router.get("/gaps", response_model=GapsResponse)
async def gaps(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None),
    days: int = Query(30, ge=7, le=365),
    min_gap_days: int = Query(2, ge=1, le=30),
    limit_tickers: int = Query(500, ge=1, le=5000),
) -> GapsResponse:
    market_value = _resolve_market(market)
    end = date.today()
    start = end - timedelta(days=days)

    # Pull active tickers
    sec_stmt = select(Security.market, Security.ticker).where(Security.is_active.is_(True)).limit(limit_tickers)
    if market_value:
        sec_stmt = sec_stmt.where(Security.market == market_value)
    sec_pairs = list((await db.execute(sec_stmt)).all())
    if not sec_pairs:
        return GapsResponse(
            market=market_value, window_start=start.isoformat(),
            window_end=end.isoformat(),
            total_tickers_checked=0, tickers_with_gaps=0, windows=[],
        )

    # Pull all DailyPrice rows in window for these tickers
    price_rows = list((await db.execute(
        select(DailyPrice.market, DailyPrice.ticker, DailyPrice.trade_date)
        .where(and_(
            DailyPrice.market.in_({m for m, _ in sec_pairs}),
            DailyPrice.ticker.in_({t for _, t in sec_pairs}),
            DailyPrice.trade_date >= start,
            DailyPrice.trade_date <= end,
        ))
    )).all())
    by_ticker: dict[tuple[str, str], list[date]] = {}
    for m, t, d in price_rows:
        by_ticker.setdefault((m, t), []).append(d)

    all_windows: list[GapWindowOut] = []
    tickers_with_gaps = 0
    for m, t in sec_pairs:
        actual = by_ticker.get((m, t), [])
        windows = find_gaps(
            market=m, ticker=t,
            actual_dates=actual,
            is_trading_day=is_weekday,
            start=start, end=end,
            min_gap_days=min_gap_days,
        )
        if windows:
            tickers_with_gaps += 1
        for w in windows:
            all_windows.append(GapWindowOut(
                market=w.market, ticker=w.ticker,
                start=w.start.isoformat(),
                end=w.end.isoformat(),
                days=w.days,
            ))

    # Sort by gap length desc
    all_windows.sort(key=lambda w: -w.days)
    return GapsResponse(
        market=market_value,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        total_tickers_checked=len(sec_pairs),
        tickers_with_gaps=tickers_with_gaps,
        windows=all_windows,
    )


def _resolve_market(market: Optional[str]) -> Optional[str]:
    if market is None:
        return None
    try:
        return Market(market.upper()).value
    except ValueError as e:
        raise HTTPException(400, f"unknown market: {market}") from e
