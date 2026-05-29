"""Pure replay simulator — no DB, no FastAPI.

Walks a chronological list of decision signals (BUY/SELL/HOLD/REJECTED)
and simulates the resulting paper portfolio under a simple long-only
rule:

- On BUY for a ticker we don't already hold: open at signal_date close.
- On SELL/EXIT for a ticker we DO hold: close at signal_date close.
- HOLD / REJECTED / unmatched SELL: skipped.
- Position sizing is fixed-fraction of *current equity* per trade
  (configurable, default 5%). Cash can't go negative — if we can't
  afford the position-size at signal time, the BUY is skipped.

The output is a sequence of ``SimulatedTrade`` records (one per closed
position) plus any positions still open at the end. Equity-curve math
(``brokers.paper_equity``) consumes the closed trades directly.

This deliberately omits realistic slippage / commission so the result
reflects the *signal quality alone*. Layering execution friction on top
is a future iteration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Iterable, Literal, Optional


Action = Literal["BUY", "SELL", "HOLD", "EXIT", "REJECTED", "REDUCE"]


@dataclass(frozen=True)
class DecisionSignal:
    """One historical decision the replay should consider.

    The price callable is invoked at simulation time, NOT here, so we
    don't pin a specific price into the signal — the same signal can
    be replayed against different price oracles (e.g. dividend-adjusted
    vs raw)."""
    ts: datetime
    market: str
    ticker: str
    action: Action


@dataclass(frozen=True)
class SimulatedTrade:
    """A round-trip — synthetic equivalent of ``PaperTrade``."""
    market: str
    ticker: str
    side: str                       # always 'BUY' for now (long-only)
    volume: float
    entry_price: float
    exit_price: float
    entry_ts: datetime
    exit_ts: datetime
    pnl: float


@dataclass(frozen=True)
class OpenPosition:
    market: str
    ticker: str
    volume: float
    entry_price: float
    entry_ts: datetime


@dataclass
class ReplayResult:
    closed_trades: list[SimulatedTrade]
    open_positions: list[OpenPosition]
    skipped_signals: int            # signals we couldn't act on
    cash_remaining: float
    initial_balance: float


# ──────────────────────────────────────────────────────────────────────


def replay(
    signals: Iterable[DecisionSignal],
    *,
    price_at: Callable[[str, str, date], Optional[float]],
    initial_balance: float,
    position_fraction: float = 0.05,
    min_position_value: float = 100.0,
) -> ReplayResult:
    """Run the simulator.

    Parameters
    ----------
    signals
        Decisions to consider. Will be sorted by ``ts`` ascending.
    price_at(market, ticker, date) -> close price or None
        Caller-provided lookup. Returning ``None`` means "no price for
        this day" → the signal is skipped. A real backtest backs this
        with ``DailyPrice``; tests use an in-memory dict.
    initial_balance
        Starting cash in account base currency.
    position_fraction
        Fraction of CURRENT equity (cash + open MtM) to deploy on each
        BUY. 0.05 → 5% per position; with default 20-ish positions max
        this is fully invested. Caller can tighten.
    min_position_value
        Floor on the per-trade allocation — if the fractional sizing
        falls below this, we skip rather than open a tiny noisy
        position.
    """
    if initial_balance <= 0:
        raise ValueError("initial_balance must be positive")
    if not (0 < position_fraction <= 1):
        raise ValueError("position_fraction must be in (0, 1]")

    ordered = sorted(signals, key=lambda s: s.ts)
    cash = float(initial_balance)
    open_by_key: dict[tuple[str, str], OpenPosition] = {}
    closed: list[SimulatedTrade] = []
    skipped = 0

    for sig in ordered:
        key = (sig.market, sig.ticker)
        day = sig.ts.date()
        price = price_at(sig.market, sig.ticker, day)
        if price is None or price <= 0:
            skipped += 1
            continue

        if sig.action == "BUY":
            if key in open_by_key:
                # Already long — pyramid not supported; skip.
                skipped += 1
                continue
            # Position size = fraction of (cash + open positions MtM)
            mtm = sum(_position_value(p, price_at) for p in open_by_key.values())
            equity = cash + mtm
            alloc = equity * position_fraction
            if alloc < min_position_value or alloc > cash:
                skipped += 1
                continue
            volume = alloc / price
            cash -= volume * price
            open_by_key[key] = OpenPosition(
                market=sig.market, ticker=sig.ticker,
                volume=volume, entry_price=price, entry_ts=sig.ts,
            )
        elif sig.action in ("SELL", "EXIT"):
            pos = open_by_key.pop(key, None)
            if pos is None:
                skipped += 1
                continue
            cash += pos.volume * price
            pnl = (price - pos.entry_price) * pos.volume
            closed.append(SimulatedTrade(
                market=sig.market, ticker=sig.ticker, side="BUY",
                volume=pos.volume,
                entry_price=pos.entry_price, exit_price=price,
                entry_ts=pos.entry_ts, exit_ts=sig.ts,
                pnl=pnl,
            ))
        else:
            # HOLD / REJECTED / REDUCE — replay engine ignores by design.
            skipped += 1

    return ReplayResult(
        closed_trades=closed,
        open_positions=list(open_by_key.values()),
        skipped_signals=skipped,
        cash_remaining=cash,
        initial_balance=float(initial_balance),
    )


def _position_value(
    pos: OpenPosition,
    price_at: Callable[[str, str, date], Optional[float]],
) -> float:
    """Mark a position at the most recent price the oracle has — uses
    the position's *entry day* as a fallback if no current quote.

    The replay doesn't track "today" because each BUY checks the
    portfolio MtM at the BUY-day price. For our simple long-only model
    that's good enough."""
    p = price_at(pos.market, pos.ticker, pos.entry_ts.date())
    if p is None:
        p = pos.entry_price
    return pos.volume * p
