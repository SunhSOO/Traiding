"""Async DB → replay glue.

Pulls historical DecisionAudit + DailyPrice rows, builds an in-memory
price oracle dict (so replay never hits the DB), runs ``replay``, and
returns the result paired with ``brokers.paper_equity`` outputs ready
for serialisation.

Kept separate from the route handler so we can backtest from a
notebook / management script later."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backtest.replay import DecisionSignal, ReplayResult, replay
from brokers.paper_equity import (
    EquityPoint, EquitySummary, TradeRecord,
    build_equity_curve, summarize,
)
from core.models.audit import DecisionAudit
from core.models.prices import DailyPrice


@dataclass
class BacktestRun:
    result: ReplayResult
    curve: list[EquityPoint]
    summary: EquitySummary


async def run_backtest(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    market: Optional[str] = None,
    tickers: Optional[list[str]] = None,
    actions: Optional[list[str]] = None,
    initial_balance: float = 100_000.0,
    position_fraction: float = 0.05,
) -> BacktestRun:
    """Pull DecisionAudit rows in [start, end], simulate, summarise.

    Filters:
      - ``market`` — optional KR / US restriction
      - ``tickers`` — optional whitelist; if empty/None, all tickers
      - ``actions`` — defaults to BUY + SELL + EXIT; HOLD/REJECTED are
        irrelevant to position state. Caller can override (e.g. add
        REDUCE if the engine later honours it).
    """
    if end <= start:
        raise ValueError("end must be after start")
    actions = actions or ["BUY", "SELL", "EXIT"]

    # 1. Pull decisions
    dec_stmt = (
        select(
            DecisionAudit.decision_ts, DecisionAudit.market,
            DecisionAudit.ticker, DecisionAudit.action,
        )
        .where(and_(
            DecisionAudit.decision_ts >= start,
            DecisionAudit.decision_ts <= end,
            DecisionAudit.action.in_(actions),
        ))
        .order_by(DecisionAudit.decision_ts)
    )
    if market:
        dec_stmt = dec_stmt.where(DecisionAudit.market == market)
    if tickers:
        dec_stmt = dec_stmt.where(DecisionAudit.ticker.in_(tickers))

    decision_rows = list((await db.execute(dec_stmt)).all())
    signals = [
        DecisionSignal(ts=ts, market=m, ticker=t, action=a)
        for ts, m, t, a in decision_rows
    ]

    # 2. Pull all prices for involved tickers in the window + 5d buffer
    needed_keys = {(s.market, s.ticker) for s in signals}
    price_lookup: dict[tuple[str, str, date], float] = {}
    if needed_keys:
        # OR-clauses are fine since tickers are bounded by universe size.
        from sqlalchemy import or_

        clauses = [
            and_(DailyPrice.market == m, DailyPrice.ticker == t)
            for m, t in needed_keys
        ]
        price_rows = await db.execute(
            select(
                DailyPrice.market, DailyPrice.ticker,
                DailyPrice.trade_date, DailyPrice.close,
            )
            .where(and_(
                or_(*clauses),
                DailyPrice.trade_date >= (start - timedelta(days=5)).date(),
                DailyPrice.trade_date <= (end + timedelta(days=5)).date(),
            ))
        )
        for m, t, d, c in price_rows:
            price_lookup[(m, t, d)] = float(c)

    def price_at(m: str, t: str, d: date) -> Optional[float]:
        # Walk back up to 7 calendar days to skip weekends/holidays.
        for offset in range(0, 8):
            v = price_lookup.get((m, t, d - timedelta(days=offset)))
            if v is not None:
                return v
        return None

    # 3. Replay
    result = replay(
        signals,
        price_at=price_at,
        initial_balance=initial_balance,
        position_fraction=position_fraction,
    )

    # 4. Equity curve + summary via existing math
    records = [
        TradeRecord(exit_ts=t.exit_ts, pnl=t.pnl) for t in result.closed_trades
    ]
    curve = build_equity_curve(records, initial_balance=initial_balance)
    summary = summarize(records, curve, initial_balance=initial_balance)

    return BacktestRun(result=result, curve=curve, summary=summary)
