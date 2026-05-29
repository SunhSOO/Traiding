"""Paper-trading endpoints.

Wrappers around the ``paper_*`` tables (Phase 0.7) and the latest
``module_scores`` rows. These are the data sources the refactored
Dashboard / Portfolio / History / Analytics pages consume.

Decisions about market filtering:
- Account snapshot is currency-tagged but spans both markets.
- Positions / trades carry ``market`` so the UI can show per-market
  tabs without a separate endpoint per market.
- ``top-movers`` always takes a ``market`` query param — there is no
  cross-market default (would mix currencies and look broken).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from brokers.paper_equity import (
    EquityPoint, EquitySummary, TradeRecord,
    build_equity_curve, summarize,
)
from core.db import get_async_db
from core.models.paper import PaperAccount, PaperPosition, PaperTrade
from core.models.prices import DailyPrice
from core.models.scores import ModuleScore
from core.models.universe import Security
from core.security import CurrentUser
from core.types import Market

router = APIRouter()


def _resolve_account_name(account_name: Optional[str], market: Optional[str]) -> str:
    """Pick the right paper account when the caller didn't specify one.

    Rule:
      - Explicit ``account_name`` → use it as-is (operator-renamed
        accounts keep working).
      - Empty or the literal "default" alias → fall back to the
        per-market account: ``default-kr`` for KR, ``default-us`` for US.
      - No market either → ``default-kr`` (consistent first-load
        behaviour; the UI overrides via the market tab on first switch).

    Returns the resolved name. Routes still 404/empty if the account
    doesn't exist."""
    if account_name and account_name != "default":
        return account_name
    if market:
        m = market.upper()
        if m == "KR":
            return "default-kr"
        if m == "US":
            return "default-us"
    return "default-kr"


# ── Response shapes ──
class AccountSnapshot(BaseModel):
    id: int
    name: str
    base_currency: str
    initial_balance: float
    current_balance: float
    open_positions: int
    open_positions_kr: int
    open_positions_us: int
    closed_trades_total: int
    realised_pnl_total: float
    realised_pnl_today: float


class PositionOut(BaseModel):
    market: str
    ticker: str
    name: Optional[str]
    side: str
    volume: float
    entry_price: float
    entry_ts: str
    current_price: Optional[float]
    unrealised_pnl: Optional[float]
    unrealised_pnl_pct: Optional[float]
    sl: Optional[float]
    tp: Optional[float]


class TradeOut(BaseModel):
    id: int
    market: str
    ticker: str
    side: str
    volume: float
    entry_price: float
    exit_price: float
    entry_ts: str
    exit_ts: str
    pnl: float
    pnl_pct: float
    commission: float
    tax: float


class MoverOut(BaseModel):
    market: str
    ticker: str
    name: Optional[str]
    composite_score: float
    composite_confidence: float
    computed_ts: str


class FreshnessSummary(BaseModel):
    sources_ok: int          # last_success within 24h
    sources_stale: int       # last_success older than 24h
    sources_errored: int     # last_error set
    total: int


class EquityPointOut(BaseModel):
    date: str
    realized_pnl_day: float
    realized_pnl_cum: float
    equity: float
    drawdown: float
    trades_count_day: int


class EquitySummaryOut(BaseModel):
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_realized_pnl: float
    avg_trade_pnl: float
    best_trade_pnl: float
    worst_trade_pnl: float
    max_drawdown: float
    sharpe_like: float
    return_pct: float
    # Risk-adjusted extras (default 0 so older persisted rows still parse)
    sortino_like: float = 0.0
    calmar: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0


class EquityCurveResponse(BaseModel):
    account_name: str
    base_currency: str
    initial_balance: float
    current_equity: float
    unrealised_pnl: float
    open_positions: int
    points: list[EquityPointOut]
    summary: EquitySummaryOut


# ──────────────────────────────────────────────────────────────────────
# Account snapshot
# ──────────────────────────────────────────────────────────────────────


@router.get("/account", response_model=AccountSnapshot)
async def account_snapshot(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    name: Optional[str] = Query(None, description="Paper account name (omit → market default)"),
    market: Optional[str] = Query(None, description="KR | US — used to pick default account when name omitted"),
) -> AccountSnapshot:
    resolved = _resolve_account_name(name, market)
    acc = (await db.execute(
        select(PaperAccount).where(PaperAccount.name == resolved)
    )).scalars().first()
    if acc is None:
        # Lazily report a zero account so the UI can render before
        # the operator has bootstrapped one. Currency reflects the
        # resolved-account intent so the dashboard doesn't render KRW
        # values with a $ sign.
        fallback_ccy = "KRW" if resolved.endswith("-kr") else "USD"
        return AccountSnapshot(
            id=0, name=resolved, base_currency=fallback_ccy,
            initial_balance=0.0, current_balance=0.0,
            open_positions=0, open_positions_kr=0, open_positions_us=0,
            closed_trades_total=0, realised_pnl_total=0.0, realised_pnl_today=0.0,
        )

    # Open-position counts
    counts = (await db.execute(
        select(PaperPosition.market, func.count())
        .where(PaperPosition.account_id == acc.id)
        .group_by(PaperPosition.market)
    )).all()
    count_map = {row[0]: int(row[1]) for row in counts}

    # Trade aggregates
    today = datetime.now(UTC).date()
    closed_count = (await db.execute(
        select(func.count()).select_from(PaperTrade)
        .where(PaperTrade.account_id == acc.id)
    )).scalar() or 0
    realised_total = (await db.execute(
        select(func.coalesce(func.sum(PaperTrade.pnl), 0))
        .where(PaperTrade.account_id == acc.id)
    )).scalar() or 0
    realised_today = (await db.execute(
        select(func.coalesce(func.sum(PaperTrade.pnl), 0))
        .where(and_(
            PaperTrade.account_id == acc.id,
            func.date(PaperTrade.exit_ts) == today,
        ))
    )).scalar() or 0

    return AccountSnapshot(
        id=acc.id, name=acc.name, base_currency=acc.base_currency,
        initial_balance=float(acc.initial_balance),
        current_balance=float(acc.current_balance),
        open_positions=sum(count_map.values()),
        open_positions_kr=count_map.get("KR", 0),
        open_positions_us=count_map.get("US", 0),
        closed_trades_total=int(closed_count),
        realised_pnl_total=float(realised_total),
        realised_pnl_today=float(realised_today),
    )


# ──────────────────────────────────────────────────────────────────────
# Open positions
# ──────────────────────────────────────────────────────────────────────


@router.get("/positions", response_model=list[PositionOut])
async def positions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None, description="KR | US (omit for both)"),
    account_name: Optional[str] = Query(None),
) -> list[PositionOut]:
    resolved = _resolve_account_name(account_name, market)
    acc = (await db.execute(
        select(PaperAccount).where(PaperAccount.name == resolved)
    )).scalars().first()
    if acc is None:
        return []

    stmt = select(PaperPosition).where(PaperPosition.account_id == acc.id)
    if market:
        try:
            m = Market(market.upper())
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {market}") from e
        stmt = stmt.where(PaperPosition.market == m.value)

    rows = list((await db.execute(stmt)).scalars())
    if not rows:
        return []

    # Resolve current prices in bulk
    keys = [(r.market, r.ticker) for r in rows]
    price_map = await _latest_prices(db, keys)
    name_map = await _security_names(db, keys)

    out: list[PositionOut] = []
    for r in rows:
        current = price_map.get((r.market, r.ticker))
        upnl, upnl_pct = None, None
        if current is not None:
            sign = 1 if r.side == "BUY" else -1
            upnl = sign * (float(current) - float(r.entry_price)) * float(r.volume)
            denom = float(r.entry_price) * float(r.volume)
            if denom > 0:
                upnl_pct = upnl / denom * 100.0
        out.append(PositionOut(
            market=r.market, ticker=r.ticker, name=name_map.get((r.market, r.ticker)),
            side=r.side, volume=float(r.volume),
            entry_price=float(r.entry_price), entry_ts=r.entry_ts.isoformat(),
            current_price=float(current) if current is not None else None,
            unrealised_pnl=upnl, unrealised_pnl_pct=upnl_pct,
            sl=float(r.sl) if r.sl is not None else None,
            tp=float(r.tp) if r.tp is not None else None,
        ))
    return out


# ──────────────────────────────────────────────────────────────────────
# Closed trades
# ──────────────────────────────────────────────────────────────────────


@router.get("/trades", response_model=list[TradeOut])
async def trades(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None, description="KR | US"),
    ticker: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    account_name: Optional[str] = Query(None),
) -> list[TradeOut]:
    resolved = _resolve_account_name(account_name, market)
    acc = (await db.execute(
        select(PaperAccount).where(PaperAccount.name == resolved)
    )).scalars().first()
    if acc is None:
        return []

    stmt = (
        select(PaperTrade)
        .where(PaperTrade.account_id == acc.id)
        .order_by(desc(PaperTrade.exit_ts))
        .limit(limit)
    )
    if market:
        try:
            m = Market(market.upper())
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {market}") from e
        stmt = stmt.where(PaperTrade.market == m.value)
    if ticker:
        stmt = stmt.where(PaperTrade.ticker == ticker)

    out: list[TradeOut] = []
    for r in (await db.execute(stmt)).scalars():
        gross = float(r.entry_price) * float(r.volume)
        pnl_pct = (float(r.pnl) / gross * 100.0) if gross > 0 else 0.0
        out.append(TradeOut(
            id=r.id, market=r.market, ticker=r.ticker, side=r.side,
            volume=float(r.volume),
            entry_price=float(r.entry_price), exit_price=float(r.exit_price),
            entry_ts=r.entry_ts.isoformat(), exit_ts=r.exit_ts.isoformat(),
            pnl=float(r.pnl), pnl_pct=pnl_pct,
            commission=float(r.commission), tax=float(r.tax),
        ))
    return out


# ──────────────────────────────────────────────────────────────────────
# Top movers (highest |composite_score| today, per market)
# ──────────────────────────────────────────────────────────────────────


@router.get("/movers", response_model=list[MoverOut])
async def top_movers(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: str = Query(..., description="KR | US — required"),
    limit: int = Query(10, ge=1, le=100),
    direction: str = Query("both", description="bull | bear | both"),
) -> list[MoverOut]:
    try:
        m = Market(market.upper())
    except ValueError as e:
        raise HTTPException(400, f"unknown market: {market}") from e

    # We want the LATEST module='F'… no, latest COMPOSITE… but composite
    # isn't a row in module_scores; it lives on DecisionAudit. So define
    # "movers" here as the latest module_scores rows with the largest
    # absolute score across the three modules, restricted to ones
    # computed in the last 48 hours.
    cutoff = datetime.now(UTC) - timedelta(hours=48)

    # Group by (market, ticker, module) → latest computed_ts
    subq = (
        select(
            ModuleScore.ticker,
            ModuleScore.module,
            func.max(ModuleScore.computed_ts).label("latest_ts"),
        )
        .where(and_(
            ModuleScore.market == m.value,
            ModuleScore.computed_ts >= cutoff,
        ))
        .group_by(ModuleScore.ticker, ModuleScore.module)
        .subquery()
    )

    rows = list((await db.execute(
        select(ModuleScore)
        .join(subq, and_(
            ModuleScore.market == m.value,
            ModuleScore.ticker == subq.c.ticker,
            ModuleScore.module == subq.c.module,
            ModuleScore.computed_ts == subq.c.latest_ts,
        ))
    )).scalars())

    # Composite = mean of available module scores per ticker
    per_ticker_scores: dict[str, list[ModuleScore]] = {}
    for r in rows:
        per_ticker_scores.setdefault(r.ticker, []).append(r)

    movers: list[tuple[str, float, float, datetime]] = []
    for ticker, score_rows in per_ticker_scores.items():
        n = len(score_rows)
        score = sum(float(r.score) for r in score_rows) / n
        confidence = sum(float(r.confidence) for r in score_rows) / n
        latest = max(r.computed_ts for r in score_rows)
        movers.append((ticker, score, confidence, latest))

    if direction == "bull":
        movers = [m for m in movers if m[1] > 0]
        movers.sort(key=lambda x: -x[1])
    elif direction == "bear":
        movers = [m for m in movers if m[1] < 0]
        movers.sort(key=lambda x: x[1])
    else:
        movers.sort(key=lambda x: -abs(x[1]))

    movers = movers[:limit]
    if not movers:
        return []

    name_map = await _security_names(db, [(m.value, t) for t, *_ in movers])
    return [
        MoverOut(
            market=m.value, ticker=ticker, name=name_map.get((m.value, ticker)),
            composite_score=score, composite_confidence=conf,
            computed_ts=ts.isoformat(),
        )
        for ticker, score, conf, ts in movers
    ]


# ──────────────────────────────────────────────────────────────────────
# Equity curve
# ──────────────────────────────────────────────────────────────────────


@router.get("/equity-curve", response_model=EquityCurveResponse)
async def equity_curve(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    account_name: Optional[str] = Query(None),
    market: Optional[str] = Query(None, description="KR | US — picks default account when name omitted"),
    days: int = Query(90, ge=7, le=730, description="Window length in days"),
) -> EquityCurveResponse:
    """Day-by-day realised P&L curve plus headline metrics.

    The curve fills calendar gaps (no-trade days carry the cumulative
    forward) so the UI line stays continuous. The "current" mark-to-
    market addition is reported via ``unrealised_pnl`` separately —
    we don't graft it onto the historical curve because we have no
    historical close-of-day MtM for open positions."""
    resolved = _resolve_account_name(account_name, market)
    acc = (await db.execute(
        select(PaperAccount).where(PaperAccount.name == resolved)
    )).scalars().first()
    if acc is None:
        empty_summary = EquitySummaryOut(
            total_trades=0, wins=0, losses=0, win_rate=0.0,
            total_realized_pnl=0.0, avg_trade_pnl=0.0,
            best_trade_pnl=0.0, worst_trade_pnl=0.0,
            max_drawdown=0.0, sharpe_like=0.0, return_pct=0.0,
        )
        fallback_ccy = "KRW" if resolved.endswith("-kr") else "USD"
        return EquityCurveResponse(
            account_name=resolved, base_currency=fallback_ccy,
            initial_balance=0.0, current_equity=0.0,
            unrealised_pnl=0.0, open_positions=0,
            points=[], summary=empty_summary,
        )

    since = datetime.now(UTC) - timedelta(days=days)
    rows = list((await db.execute(
        select(PaperTrade.exit_ts, PaperTrade.pnl)
        .where(and_(
            PaperTrade.account_id == acc.id,
            PaperTrade.exit_ts >= since,
        ))
        .order_by(PaperTrade.exit_ts)
    )).all())

    records = [
        TradeRecord(exit_ts=r[0], pnl=float(r[1])) for r in rows
    ]
    initial_balance = float(acc.initial_balance)
    curve = build_equity_curve(records, initial_balance=initial_balance)
    summary = summarize(records, curve, initial_balance=initial_balance)

    # Unrealised P&L from currently open positions
    open_rows = list((await db.execute(
        select(PaperPosition).where(PaperPosition.account_id == acc.id)
    )).scalars())
    unrealised = 0.0
    if open_rows:
        keys = [(p.market, p.ticker) for p in open_rows]
        price_map = await _latest_prices(db, keys)
        for p in open_rows:
            cur = price_map.get((p.market, p.ticker))
            if cur is None:
                continue
            sign = 1 if p.side == "BUY" else -1
            unrealised += sign * (float(cur) - float(p.entry_price)) * float(p.volume)

    final_realized = curve[-1].realized_pnl_cum if curve else 0.0
    current_equity = initial_balance + final_realized + unrealised

    return EquityCurveResponse(
        account_name=acc.name,
        base_currency=acc.base_currency,
        initial_balance=initial_balance,
        current_equity=current_equity,
        unrealised_pnl=unrealised,
        open_positions=len(open_rows),
        points=[
            EquityPointOut(
                date=pt.date.isoformat(),
                realized_pnl_day=pt.realized_pnl_day,
                realized_pnl_cum=pt.realized_pnl_cum,
                equity=pt.equity,
                drawdown=pt.drawdown,
                trades_count_day=pt.trades_count_day,
            )
            for pt in curve
        ],
        summary=EquitySummaryOut(
            total_trades=summary.total_trades,
            wins=summary.wins, losses=summary.losses,
            win_rate=summary.win_rate,
            total_realized_pnl=summary.total_realized_pnl,
            avg_trade_pnl=summary.avg_trade_pnl,
            best_trade_pnl=summary.best_trade_pnl,
            worst_trade_pnl=summary.worst_trade_pnl,
            max_drawdown=summary.max_drawdown,
            sharpe_like=summary.sharpe_like,
            return_pct=summary.return_pct,
            sortino_like=summary.sortino_like,
            calmar=summary.calmar,
            profit_factor=summary.profit_factor,
            expectancy=summary.expectancy,
        ),
    )


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


async def _latest_prices(
    db: AsyncSession, keys: list[tuple[str, str]],
) -> dict[tuple[str, str], float]:
    """Latest close per (market, ticker)."""
    if not keys:
        return {}
    # Build OR-clauses; manageable since open positions are at most
    # hundreds in normal operation.
    from sqlalchemy import or_

    clauses = [
        and_(DailyPrice.market == m, DailyPrice.ticker == t) for m, t in keys
    ]
    # Use a subquery for the latest trade_date per ticker
    latest_dates_subq = (
        select(
            DailyPrice.market, DailyPrice.ticker,
            func.max(DailyPrice.trade_date).label("td"),
        )
        .where(or_(*clauses))
        .group_by(DailyPrice.market, DailyPrice.ticker)
        .subquery()
    )
    rows = await db.execute(
        select(DailyPrice.market, DailyPrice.ticker, DailyPrice.close)
        .join(latest_dates_subq, and_(
            DailyPrice.market == latest_dates_subq.c.market,
            DailyPrice.ticker == latest_dates_subq.c.ticker,
            DailyPrice.trade_date == latest_dates_subq.c.td,
        ))
    )
    return {(r[0], r[1]): float(r[2]) for r in rows}


async def _security_names(
    db: AsyncSession, keys: list[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    if not keys:
        return {}
    from sqlalchemy import or_

    clauses = [and_(Security.market == m, Security.ticker == t) for m, t in keys]
    rows = await db.execute(
        select(Security.market, Security.ticker, Security.name).where(or_(*clauses))
    )
    return {(r[0], r[1]): r[2] for r in rows}
