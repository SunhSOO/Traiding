"""Backtest replay endpoint.

``POST /api/backtest/run`` — replay past DecisionAudit decisions
through the long-only simulator and return equity curve + summary.
``POST /api/backtest/rescoring`` — re-score under current weights.

Both endpoints accept ``persist=true`` to write a row to
``backtest_runs`` for later comparison. Without persist they are
pure read paths.

``GET /api/backtest/runs`` / ``/runs/{id}`` / DELETE /runs/{id}``
manage persisted runs."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backtest.rescoring_runner import run_rescoring_backtest
from backtest.runner import run_backtest
from core.db import get_async_db
from core.models.backtest import BacktestRunRow
from core.security import CurrentUser
from core.types import Market
from routes.paper import EquityPointOut, EquitySummaryOut

router = APIRouter()


class BacktestRequest(BaseModel):
    start: datetime
    end: datetime
    market: Optional[str] = Field(None, description="KR | US (omit for both)")
    tickers: Optional[list[str]] = None
    initial_balance: float = Field(100_000.0, gt=0)
    position_fraction: float = Field(0.05, gt=0, le=1.0)
    persist: bool = False
    label: Optional[str] = Field(None, max_length=128)
    notes: Optional[str] = None


class BacktestTradeOut(BaseModel):
    market: str
    ticker: str
    side: str
    volume: float
    entry_price: float
    exit_price: float
    entry_ts: str
    exit_ts: str
    pnl: float


class BacktestOpenOut(BaseModel):
    market: str
    ticker: str
    volume: float
    entry_price: float
    entry_ts: str


class BacktestResponse(BaseModel):
    initial_balance: float
    final_equity: float
    cash_remaining: float
    skipped_signals: int
    closed_trades: list[BacktestTradeOut]
    open_positions: list[BacktestOpenOut]
    points: list[EquityPointOut]
    summary: EquitySummaryOut


class RescoringRequest(BaseModel):
    start: datetime
    end: datetime
    market: Optional[str] = Field(None, description="KR | US (omit for both)")
    tickers: Optional[list[str]] = None
    initial_balance: float = Field(100_000.0, gt=0)
    position_fraction: float = Field(0.05, gt=0, le=1.0)
    # Rescoring knobs
    buy_threshold: float = 25.0
    sell_threshold: float = -25.0
    min_overall_confidence: float = Field(0.40, ge=0.0, le=1.0)
    decision_cooldown_days: int = Field(1, ge=0, le=30)
    score_staleness_hours: int = Field(48, ge=1, le=720)
    use_learned_weights: bool = True
    weight_override: Optional[dict[str, float]] = Field(
        None,
        description='{"F":0.4,"T":0.4,"I":0.2}-shaped global override.',
    )
    restrict_to_indices: Optional[list[str]] = Field(
        None,
        description=(
            "Survivorship-bias guard. If set (e.g. ['KOSPI200']), "
            "drop synthetic decisions where the ticker wasn't a "
            "member of the index on the decision date."
        ),
    )
    persist: bool = False
    label: Optional[str] = Field(None, max_length=128)
    notes: Optional[str] = None


@router.post("/run", response_model=BacktestResponse)
async def run(
    body: BacktestRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> BacktestResponse:
    if body.end <= body.start:
        raise HTTPException(400, "end must be after start")
    if body.market is not None:
        try:
            m = Market(body.market.upper())
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {body.market}") from e
        market_value = m.value
    else:
        market_value = None

    # Enforce TZ-aware datetimes; default to UTC if naive.
    start = body.start if body.start.tzinfo else body.start.replace(tzinfo=UTC)
    end = body.end if body.end.tzinfo else body.end.replace(tzinfo=UTC)

    run_result = await run_backtest(
        db,
        start=start, end=end,
        market=market_value,
        tickers=body.tickers or None,
        initial_balance=body.initial_balance,
        position_fraction=body.position_fraction,
    )
    if body.persist:
        await _persist_run(
            db,
            mode="replay", body=body, run_result=run_result,
            market_value=market_value, start=start, end=end,
            triggered_by=user.username if user else "anonymous",
            config_extra={},
        )
    return _build_response(run_result, body.initial_balance)


@router.post("/rescoring", response_model=BacktestResponse)
async def rescoring(
    body: RescoringRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> BacktestResponse:
    """Apply current (or override) weights to historical module_scores
    and replay the resulting decisions.

    Unlike ``/run`` which replays *actual past decisions*, this endpoint
    re-generates the decisions from the score history under whatever
    weight policy you specify — the canonical "would today's model have
    worked yesterday?" test."""
    if body.end <= body.start:
        raise HTTPException(400, "end must be after start")
    market_value: Optional[str] = None
    if body.market is not None:
        try:
            m = Market(body.market.upper())
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {body.market}") from e
        market_value = m.value

    start = body.start if body.start.tzinfo else body.start.replace(tzinfo=UTC)
    end = body.end if body.end.tzinfo else body.end.replace(tzinfo=UTC)

    run_result = await run_rescoring_backtest(
        db,
        start=start, end=end,
        market=market_value,
        tickers=body.tickers or None,
        initial_balance=body.initial_balance,
        position_fraction=body.position_fraction,
        buy_threshold=body.buy_threshold,
        sell_threshold=body.sell_threshold,
        min_overall_confidence=body.min_overall_confidence,
        decision_cooldown_days=body.decision_cooldown_days,
        score_staleness_hours=body.score_staleness_hours,
        use_learned_weights=body.use_learned_weights,
        weight_override=body.weight_override,
        restrict_to_indices=body.restrict_to_indices,
    )
    if body.persist:
        await _persist_run(
            db,
            mode="rescoring", body=body, run_result=run_result,
            market_value=market_value, start=start, end=end,
            triggered_by=user.username if user else "anonymous",
            config_extra={
                "buy_threshold": body.buy_threshold,
                "sell_threshold": body.sell_threshold,
                "min_overall_confidence": body.min_overall_confidence,
                "decision_cooldown_days": body.decision_cooldown_days,
                "score_staleness_hours": body.score_staleness_hours,
                "use_learned_weights": body.use_learned_weights,
                "weight_override": body.weight_override,
                "restrict_to_indices": body.restrict_to_indices,
            },
        )
    return _build_response(run_result, body.initial_balance)


def _build_response(run_result, initial_balance: float) -> BacktestResponse:
    final_equity = (
        run_result.curve[-1].equity if run_result.curve else initial_balance
    )
    return BacktestResponse(
        initial_balance=initial_balance,
        final_equity=final_equity,
        cash_remaining=run_result.result.cash_remaining,
        skipped_signals=run_result.result.skipped_signals,
        closed_trades=[
            BacktestTradeOut(
                market=t.market, ticker=t.ticker, side=t.side,
                volume=t.volume,
                entry_price=t.entry_price, exit_price=t.exit_price,
                entry_ts=t.entry_ts.isoformat(),
                exit_ts=t.exit_ts.isoformat(),
                pnl=t.pnl,
            )
            for t in run_result.result.closed_trades
        ],
        open_positions=[
            BacktestOpenOut(
                market=p.market, ticker=p.ticker,
                volume=p.volume, entry_price=p.entry_price,
                entry_ts=p.entry_ts.isoformat(),
            )
            for p in run_result.result.open_positions
        ],
        points=[
            EquityPointOut(
                date=pt.date.isoformat(),
                realized_pnl_day=pt.realized_pnl_day,
                realized_pnl_cum=pt.realized_pnl_cum,
                equity=pt.equity,
                drawdown=pt.drawdown,
                trades_count_day=pt.trades_count_day,
            )
            for pt in run_result.curve
        ],
        summary=EquitySummaryOut(
            total_trades=run_result.summary.total_trades,
            wins=run_result.summary.wins,
            losses=run_result.summary.losses,
            win_rate=run_result.summary.win_rate,
            total_realized_pnl=run_result.summary.total_realized_pnl,
            avg_trade_pnl=run_result.summary.avg_trade_pnl,
            best_trade_pnl=run_result.summary.best_trade_pnl,
            worst_trade_pnl=run_result.summary.worst_trade_pnl,
            max_drawdown=run_result.summary.max_drawdown,
            sharpe_like=run_result.summary.sharpe_like,
            return_pct=run_result.summary.return_pct,
            sortino_like=run_result.summary.sortino_like,
            calmar=run_result.summary.calmar,
            profit_factor=run_result.summary.profit_factor,
            expectancy=run_result.summary.expectancy,
        ),
    )


# ──────────────────────────────────────────────────────────────────────
# Persisted runs (backtest_runs table)
# ──────────────────────────────────────────────────────────────────────


class BacktestRunListItem(BaseModel):
    id: str
    label: str
    mode: str
    market: Optional[str]
    window_start: str
    window_end: str
    initial_balance: float
    final_equity: float
    total_trades: int
    win_rate: Optional[float]
    max_drawdown: Optional[float]
    sharpe_like: Optional[float]
    return_pct: Optional[float]
    triggered_by: str
    notes: Optional[str]
    created_at: str


class BacktestRunDetail(BacktestRunListItem):
    total_realized_pnl: Optional[float]
    avg_trade_pnl: Optional[float]
    best_trade_pnl: Optional[float]
    worst_trade_pnl: Optional[float]
    skipped_signals: int
    config_snapshot: Optional[dict] = None
    equity_points: Optional[list] = None


async def _persist_run(
    db: AsyncSession,
    *,
    mode: str,
    body,                       # BacktestRequest or RescoringRequest
    run_result,
    market_value: Optional[str],
    start: datetime,
    end: datetime,
    triggered_by: str,
    config_extra: dict,
) -> None:
    """Write one row to ``backtest_runs``. Best-effort: a persistence
    failure must NOT corrupt the response the caller already computed."""
    label = body.label or _auto_label(mode, market_value, start, end)
    final_equity = (
        run_result.curve[-1].equity if run_result.curve else body.initial_balance
    )
    config_snapshot = {
        "mode": mode,
        "market": market_value,
        "tickers": list(body.tickers) if body.tickers else None,
        "initial_balance": body.initial_balance,
        "position_fraction": body.position_fraction,
        **config_extra,
    }
    # Trim equity_points to just (date, equity, drawdown) so the row
    # stays small even on 5y windows.
    points = [
        {"date": pt.date.isoformat(),
         "equity": float(pt.equity),
         "drawdown": float(pt.drawdown)}
        for pt in run_result.curve
    ]
    row = BacktestRunRow(
        label=label[:128],
        mode=mode,
        market=market_value,
        window_start=start,
        window_end=end,
        initial_balance=float(body.initial_balance),
        final_equity=float(final_equity),
        total_trades=run_result.summary.total_trades,
        win_rate=float(run_result.summary.win_rate),
        total_realized_pnl=float(run_result.summary.total_realized_pnl),
        avg_trade_pnl=float(run_result.summary.avg_trade_pnl),
        best_trade_pnl=float(run_result.summary.best_trade_pnl),
        worst_trade_pnl=float(run_result.summary.worst_trade_pnl),
        max_drawdown=float(run_result.summary.max_drawdown),
        sharpe_like=float(run_result.summary.sharpe_like),
        return_pct=float(run_result.summary.return_pct),
        skipped_signals=run_result.result.skipped_signals,
        config_snapshot=config_snapshot,
        equity_points=points,
        triggered_by=triggered_by,
        notes=body.notes,
    )
    db.add(row)
    await db.flush()


def _auto_label(mode: str, market: Optional[str], start: datetime, end: datetime) -> str:
    parts = [mode, market or "ALL",
             start.date().isoformat(), "→", end.date().isoformat()]
    return " ".join(parts)


@router.get("/runs", response_model=list[BacktestRunListItem])
async def list_runs(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    mode: Optional[str] = None,
    market: Optional[str] = None,
    limit: int = 100,
) -> list[BacktestRunListItem]:
    stmt = (
        select(BacktestRunRow)
        .order_by(desc(BacktestRunRow.created_at))
        .limit(min(max(limit, 1), 500))
    )
    if mode:
        stmt = stmt.where(BacktestRunRow.mode == mode)
    if market:
        try:
            mv = Market(market.upper()).value
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {market}") from e
        stmt = stmt.where(BacktestRunRow.market == mv)
    rows = list((await db.execute(stmt)).scalars())
    return [_row_to_list_item(r) for r in rows]


@router.get("/runs/{run_id}", response_model=BacktestRunDetail)
async def get_run(
    run_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> BacktestRunDetail:
    try:
        rid = uuid.UUID(run_id)
    except ValueError as e:
        raise HTTPException(400, "invalid run id") from e
    row = await db.get(BacktestRunRow, rid)
    if row is None:
        raise HTTPException(404, "not found")
    return _row_to_detail(row)


@router.delete("/runs/{run_id}")
async def delete_run(
    run_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> dict:
    try:
        rid = uuid.UUID(run_id)
    except ValueError as e:
        raise HTTPException(400, "invalid run id") from e
    row = await db.get(BacktestRunRow, rid)
    if row is None:
        raise HTTPException(404, "not found")
    await db.delete(row)
    return {"deleted": run_id}


def _row_to_list_item(r: BacktestRunRow) -> BacktestRunListItem:
    return BacktestRunListItem(
        id=str(r.id),
        label=r.label,
        mode=r.mode,
        market=r.market,
        window_start=r.window_start.isoformat(),
        window_end=r.window_end.isoformat(),
        initial_balance=float(r.initial_balance),
        final_equity=float(r.final_equity),
        total_trades=r.total_trades,
        win_rate=float(r.win_rate) if r.win_rate is not None else None,
        max_drawdown=float(r.max_drawdown) if r.max_drawdown is not None else None,
        sharpe_like=float(r.sharpe_like) if r.sharpe_like is not None else None,
        return_pct=float(r.return_pct) if r.return_pct is not None else None,
        triggered_by=r.triggered_by,
        notes=r.notes,
        created_at=r.created_at.isoformat(),
    )


def _row_to_detail(r: BacktestRunRow) -> BacktestRunDetail:
    base = _row_to_list_item(r).model_dump()
    return BacktestRunDetail(
        **base,
        total_realized_pnl=float(r.total_realized_pnl) if r.total_realized_pnl is not None else None,
        avg_trade_pnl=float(r.avg_trade_pnl) if r.avg_trade_pnl is not None else None,
        best_trade_pnl=float(r.best_trade_pnl) if r.best_trade_pnl is not None else None,
        worst_trade_pnl=float(r.worst_trade_pnl) if r.worst_trade_pnl is not None else None,
        skipped_signals=r.skipped_signals,
        config_snapshot=r.config_snapshot,
        equity_points=r.equity_points,
    )
