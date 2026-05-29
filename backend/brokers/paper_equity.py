"""Paper equity-curve math — pure functions, no DB.

Inputs are simple iterables (list of trade dicts / namedtuples); outputs
are list[EquityPoint] + summary metrics. Kept separate from
``routes/paper.py`` so the math can be unit-tested without a DB or
FastAPI dependency, and reused later by the backtest harness.

Definitions:

- *Equity curve* = the time series of (cumulative realised P&L +
  initial balance) sampled per day. We do NOT mark-to-market open
  positions on each historical day — there is no historical close
  price for open positions snapshotted day-by-day, and the daily
  ingestion job doesn't store mid-day P&L. The "current" point at the
  tail is the only mark-to-market point.

- *Drawdown* at day t = (peak_so_far - equity_t) / peak_so_far,
  expressed as a positive fraction. Max drawdown is the worst.

- *Sharpe-like* = mean(daily_return) / stdev(daily_return) * √252.
  This is a rough surrogate, not a true Sharpe (no risk-free rate
  subtraction); we name it ``sharpe_like`` so the UI doesn't mislead.

Per-currency note: P&L values are passed in already-normalised to the
account's base currency. Mixing currencies happens at trade-write
time (``brokers/paper_persistence.py``), not here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional


@dataclass(frozen=True)
class TradeRecord:
    """Minimal trade row used by the equity-curve math.

    The route adapts ``PaperTrade`` rows to this shape so the math
    layer doesn't depend on SQLAlchemy."""
    exit_ts: datetime
    pnl: float


@dataclass(frozen=True)
class EquityPoint:
    date: date
    realized_pnl_day: float        # sum of trade.pnl exiting that day
    realized_pnl_cum: float        # running total
    equity: float                  # initial_balance + realized_pnl_cum
    drawdown: float                # 0..1 from peak so far
    trades_count_day: int


@dataclass(frozen=True)
class EquitySummary:
    total_trades: int
    wins: int
    losses: int
    win_rate: float                # 0..1
    total_realized_pnl: float
    avg_trade_pnl: float
    best_trade_pnl: float
    worst_trade_pnl: float
    max_drawdown: float            # 0..1
    sharpe_like: float             # daily Sharpe ×√252, no risk-free
    return_pct: float              # final_equity / initial - 1
    # ── Risk-adjusted metrics (annualised) ──
    sortino_like: float = 0.0       # downside-deviation variant of Sharpe
    calmar: float = 0.0             # annual return / max drawdown
    profit_factor: float = 0.0      # gross wins / gross losses (1.0 = breakeven)
    expectancy: float = 0.0         # win_rate*avg_win + (1-win_rate)*avg_loss


# ──────────────────────────────────────────────────────────────────────


def build_equity_curve(
    trades: Iterable[TradeRecord],
    *,
    initial_balance: float,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> list[EquityPoint]:
    """Bucket trades by exit_ts.date() and build a day-by-day curve.

    If ``start``/``end`` are omitted, the curve spans the first trade's
    day to the last trade's day. Days with no trades still get a point
    (carries the cumulative forward) so the chart line stays flat
    instead of showing gaps.
    """
    rows = sorted(trades, key=lambda t: t.exit_ts)
    if not rows:
        return []

    first_day = start or rows[0].exit_ts.date()
    last_day = end or rows[-1].exit_ts.date()
    if first_day > last_day:
        return []

    # Bucket by day
    per_day_pnl: dict[date, float] = {}
    per_day_count: dict[date, int] = {}
    for t in rows:
        d = t.exit_ts.date()
        if d < first_day or d > last_day:
            continue
        per_day_pnl[d] = per_day_pnl.get(d, 0.0) + float(t.pnl)
        per_day_count[d] = per_day_count.get(d, 0) + 1

    out: list[EquityPoint] = []
    cum = 0.0
    peak = initial_balance
    d = first_day
    while d <= last_day:
        day_pnl = per_day_pnl.get(d, 0.0)
        cum += day_pnl
        equity = initial_balance + cum
        if equity > peak:
            peak = equity
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        out.append(EquityPoint(
            date=d,
            realized_pnl_day=day_pnl,
            realized_pnl_cum=cum,
            equity=equity,
            drawdown=drawdown,
            trades_count_day=per_day_count.get(d, 0),
        ))
        d += timedelta(days=1)
    return out


def summarize(
    trades: Iterable[TradeRecord],
    curve: list[EquityPoint],
    *,
    initial_balance: float,
) -> EquitySummary:
    """Reduce trades + curve into headline numbers for the dashboard.

    ``trades`` and ``curve`` should be the same input — passed
    separately so callers that already computed the curve don't pay
    for a second pass."""
    trade_list = list(trades)
    n = len(trade_list)
    if n == 0:
        return EquitySummary(
            total_trades=0, wins=0, losses=0, win_rate=0.0,
            total_realized_pnl=0.0, avg_trade_pnl=0.0,
            best_trade_pnl=0.0, worst_trade_pnl=0.0,
            max_drawdown=0.0, sharpe_like=0.0, return_pct=0.0,
        )

    pnls = [float(t.pnl) for t in trade_list]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    total = sum(pnls)
    max_dd = max((p.drawdown for p in curve), default=0.0)

    # Trade-level metrics
    gross_wins = sum(p for p in pnls if p > 0)
    gross_losses = -sum(p for p in pnls if p < 0)    # positive number
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else 0.0
    avg_win = (gross_wins / wins) if wins > 0 else 0.0
    avg_loss = (gross_losses / losses) if losses > 0 else 0.0
    win_rate = wins / n
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    # Daily returns from the equity curve drive Sharpe / Sortino / Calmar
    sharpe = 0.0
    sortino = 0.0
    if len(curve) >= 2:
        returns: list[float] = []
        prev = curve[0].equity
        for pt in curve[1:]:
            if prev > 0:
                returns.append(pt.equity / prev - 1.0)
            prev = pt.equity
        if returns:
            mean = sum(returns) / len(returns)
            # Sharpe: full-return stdev
            var = sum((r - mean) ** 2 for r in returns) / len(returns)
            stdev = math.sqrt(var) if var > 0 else 0.0
            if stdev > 0:
                sharpe = mean / stdev * math.sqrt(252)
            # Sortino: downside-only stdev (penalises losses, ignores upside vol)
            downside = [r for r in returns if r < 0]
            if downside:
                d_var = sum(r ** 2 for r in downside) / len(returns)
                d_stdev = math.sqrt(d_var) if d_var > 0 else 0.0
                if d_stdev > 0:
                    sortino = mean / d_stdev * math.sqrt(252)
            elif mean > 0:
                # No losing day at all + positive mean — convention is to
                # report a very high but finite number rather than infinity
                # so the UI table doesn't blow up.
                sortino = 99.0

    final_equity = curve[-1].equity if curve else initial_balance
    return_pct = (final_equity / initial_balance - 1.0) if initial_balance > 0 else 0.0

    # Calmar: annualised return / max drawdown. Annualise using window length.
    calmar = 0.0
    if max_dd > 0 and len(curve) >= 2:
        days = max(len(curve), 1)
        annualised = (1 + return_pct) ** (365.0 / days) - 1.0
        calmar = annualised / max_dd

    return EquitySummary(
        total_trades=n,
        wins=wins, losses=losses,
        win_rate=win_rate,
        total_realized_pnl=total,
        avg_trade_pnl=total / n,
        best_trade_pnl=max(pnls),
        worst_trade_pnl=min(pnls),
        max_drawdown=max_dd,
        sharpe_like=sharpe,
        return_pct=return_pct,
        sortino_like=sortino,
        calmar=calmar,
        profit_factor=profit_factor,
        expectancy=expectancy,
    )
