"""Paper (virtual) broker.

Simulates fills against a price oracle the operator supplies (live
quote stream, last close, or backtest bar). Costs come from each
market's adapter so they're realistic. Persists positions/trades to
``paper_positions`` and ``paper_trades`` tables via SQLAlchemy.

Design notes:

- The broker is split into two layers:

  * :class:`PaperBrokerLogic` — pure functions. Given an intent +
    quoted price + current account state, returns the new state and
    the fill result. Easy to unit-test, no DB.
  * :class:`PaperBroker` — wraps the logic with persistence. Used
    by FastAPI routes and the runtime layer.

  The logic layer is what most tests exercise; the persistence layer
  is covered by integration tests that need PostgreSQL up.

- Slippage model: market orders fill at quote ± ``slippage_bps``
  centered on the bid/ask spread mid-price. Default 1 bps each side.

- Short selling: supported. Brokers in KR have restrictions on
  short-selling individual equities (uptick rule, naked-short bans);
  the paper broker does NOT enforce those — Phase 0.5 risk engine
  is responsible for that check.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Optional, Protocol

from core.types import Market
from markets import get_adapter
from brokers.base import (
    AccountSnapshot,
    BrokerAdapter,
    ExecutionResult,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionSnapshot,
)


DEFAULT_SLIPPAGE_BPS = 1.0


class PriceOracle(Protocol):
    """Anything that can quote the current price for a ticker at a
    point in time. In paper mode this might be the latest cached
    quote; in backtest mode it's the historical bar."""

    def quote(self, market: Market, ticker: str, *, as_of: datetime) -> "Quote": ...


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    last: float
    ts: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bps(self) -> float:
        if self.mid <= 0:
            return 0.0
        return (self.ask - self.bid) / self.mid * 10_000


# ──────────────────────────────────────────────────────────────────────
# Pure logic layer (no DB)
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _OpenPosition:
    market: Market
    ticker: str
    side: OrderSide
    volume: float
    entry_price: float
    entry_ts: datetime
    sl: Optional[float] = None
    tp: Optional[float] = None


@dataclass(frozen=True)
class _ClosedTrade:
    market: Market
    ticker: str
    side: OrderSide
    volume: float
    entry_price: float
    exit_price: float
    entry_ts: datetime
    exit_ts: datetime
    pnl: float
    commission: float
    tax: float
    slippage_bps: float


class PaperBrokerLogic:
    """Stateful but DB-free. Holds positions in a dict keyed by
    (market, ticker). Subclasses with persistence wrap this."""

    def __init__(
        self,
        *,
        base_currency: str,
        initial_balance: float,
        slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    ):
        self.base_currency = base_currency
        self._initial_balance = initial_balance
        self._cash = initial_balance
        self._positions: dict[tuple[Market, str], _OpenPosition] = {}
        self._slippage_bps = slippage_bps

    # ── Public API ──
    def open(
        self,
        intent: OrderIntent,
        quote: Quote,
        *,
        now: datetime,
    ) -> tuple[ExecutionResult, Optional[_OpenPosition]]:
        if intent.order_type is not OrderType.MARKET:
            return self._fail(intent, "PaperBroker only supports MARKET orders"), None

        key = (intent.market, intent.ticker)
        if key in self._positions:
            return self._fail(intent, f"Already have open position on {intent.market.value}:{intent.ticker}"), None

        adapter = get_adapter(intent.market)
        fill_price = self._apply_slippage(quote, intent.side)
        gross = fill_price * intent.volume
        if gross <= 0:
            return self._fail(intent, "Computed gross value is non-positive"), None

        tax_bd = adapter.compute_tax(side=intent.side.value, gross_value=gross)

        # Cash check (long only — shorts use margin which we model trivially
        # as "no cash impact at open" since we're not modelling overnight
        # borrow fees here).
        cash_impact = gross + tax_bd.total if intent.side is OrderSide.BUY else -gross + tax_bd.total
        if intent.side is OrderSide.BUY and cash_impact > self._cash:
            return self._fail(intent, f"Insufficient cash: need {cash_impact:.2f}, have {self._cash:.2f}"), None

        self._cash -= cash_impact
        pos = _OpenPosition(
            market=intent.market,
            ticker=intent.ticker,
            side=intent.side,
            volume=intent.volume,
            entry_price=fill_price,
            entry_ts=now,
            sl=intent.sl,
            tp=intent.tp,
        )
        self._positions[key] = pos
        return (
            ExecutionResult(
                ok=True,
                intent=intent,
                fill_price=fill_price,
                fill_volume=intent.volume,
                fill_ts=now,
                commission=tax_bd.commission,
                tax=tax_bd.transaction_tax + tax_bd.other_fees,
                slippage_bps=self._slippage_bps,
                venue_order_id=f"paper-{key[0].value}-{key[1]}-{now.isoformat()}",
            ),
            pos,
        )

    def close(
        self,
        market: Market,
        ticker: str,
        quote: Quote,
        *,
        now: datetime,
        comment: str = "",
        decision_audit_id: Optional[str] = None,
    ) -> tuple[ExecutionResult, Optional[_ClosedTrade]]:
        key = (market, ticker)
        pos = self._positions.get(key)
        if pos is None:
            intent = OrderIntent(
                market=market, ticker=ticker,
                side=OrderSide.SELL, order_type=OrderType.MARKET,
                volume=0, comment=comment, decision_audit_id=decision_audit_id,
            )
            return self._fail(intent, f"No open position on {market.value}:{ticker}"), None

        adapter = get_adapter(market)
        close_side = OrderSide.SELL if pos.side is OrderSide.BUY else OrderSide.BUY
        fill_price = self._apply_slippage(quote, close_side)
        gross = fill_price * pos.volume
        tax_bd = adapter.compute_tax(side=close_side.value, gross_value=gross)

        if pos.side is OrderSide.BUY:
            pnl_gross = (fill_price - pos.entry_price) * pos.volume
            cash_impact = -(gross - tax_bd.total)
        else:
            pnl_gross = (pos.entry_price - fill_price) * pos.volume
            cash_impact = gross + tax_bd.total

        # Realised cash adjustment: receive proceeds (or pay buy-back),
        # then deduct costs.
        if pos.side is OrderSide.BUY:
            self._cash += gross - tax_bd.total
        else:
            self._cash -= gross + tax_bd.total

        pnl_net = pnl_gross - tax_bd.total
        trade = _ClosedTrade(
            market=market, ticker=ticker, side=pos.side, volume=pos.volume,
            entry_price=pos.entry_price, exit_price=fill_price,
            entry_ts=pos.entry_ts, exit_ts=now,
            pnl=pnl_net,
            commission=tax_bd.commission,
            tax=tax_bd.transaction_tax + tax_bd.other_fees,
            slippage_bps=self._slippage_bps,
        )
        del self._positions[key]

        intent = OrderIntent(
            market=market, ticker=ticker, side=close_side,
            order_type=OrderType.MARKET, volume=pos.volume,
            comment=comment, decision_audit_id=decision_audit_id,
        )
        return (
            ExecutionResult(
                ok=True,
                intent=intent,
                fill_price=fill_price,
                fill_volume=pos.volume,
                fill_ts=now,
                commission=trade.commission,
                tax=trade.tax,
                slippage_bps=self._slippage_bps,
                venue_order_id=f"paper-close-{market.value}-{ticker}-{now.isoformat()}",
                extras={"pnl": pnl_net},
            ),
            trade,
        )

    def mark_to_market(self, quote_for: Callable[[Market, str], Quote]) -> float:
        """Return current equity (cash + unrealised P&L) given a
        quote-lookup function. Used by :meth:`PaperBroker.get_account`."""
        unrealised = 0.0
        for pos in self._positions.values():
            q = quote_for(pos.market, pos.ticker)
            if pos.side is OrderSide.BUY:
                unrealised += (q.mid - pos.entry_price) * pos.volume
            else:
                unrealised += (pos.entry_price - q.mid) * pos.volume
        return self._cash + unrealised

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def initial_balance(self) -> float:
        return self._initial_balance

    @property
    def open_positions(self) -> list[_OpenPosition]:
        return list(self._positions.values())

    # ── Internals ──
    def _apply_slippage(self, quote: Quote, side: OrderSide) -> float:
        bps = self._slippage_bps / 10_000
        if side is OrderSide.BUY:
            return quote.ask * (1 + bps)
        return quote.bid * (1 - bps)

    @staticmethod
    def _fail(intent: OrderIntent, msg: str) -> ExecutionResult:
        return ExecutionResult(ok=False, intent=intent, error=msg)


# ──────────────────────────────────────────────────────────────────────
# Persistence wrapper (used by routes/runtime; integration-tested)
# ──────────────────────────────────────────────────────────────────────


class PaperBroker(BrokerAdapter):
    """SQLAlchemy-backed paper broker.

    Composes :class:`PaperBrokerLogic` (in-memory state machine) with
    :mod:`brokers.paper_persistence` (DB writes). The broker is owned
    by a single account; multi-account support is opaque to callers
    (they get one broker per account).

    Persistence model:
    - Open / close round-trip each open a session via ``session_factory``
      and write through the persistence helpers. The session is committed
      automatically at the ``with`` block exit (see :func:`core.db.session_scope`).
    - Failures inside the DB write block raise to the caller; the
      in-memory logic is NOT rolled back, which is intentional: the
      operator should not see ghost positions after a write fail.
      Instead the runner's audit row captures ``execution_result.ok=False``
      and the next decision iteration re-evaluates the ticker.
    """

    name = "paper"

    def __init__(
        self,
        logic: PaperBrokerLogic,
        oracle: PriceOracle,
        *,
        session_factory=None,
        account_id: Optional[int] = None,
    ):
        self._logic = logic
        self._oracle = oracle
        self._session_factory = session_factory
        self._account_id = account_id

    def execute(self, intent: OrderIntent) -> ExecutionResult:
        from datetime import UTC
        now = datetime.now(UTC)
        quote = self._oracle.quote(intent.market, intent.ticker, as_of=now)
        result, pos = self._logic.open(intent, quote, now=now)
        if result.ok and pos is not None:
            self._persist_open(pos, result)
        return result

    def close_position(
        self,
        market: Market,
        ticker: str,
        *,
        comment: str = "",
        decision_audit_id: Optional[str] = None,
    ) -> ExecutionResult:
        from datetime import UTC
        now = datetime.now(UTC)
        quote = self._oracle.quote(market, ticker, as_of=now)
        result, trade = self._logic.close(
            market, ticker, quote, now=now,
            comment=comment, decision_audit_id=decision_audit_id,
        )
        if result.ok and trade is not None:
            self._persist_close(trade, result, decision_audit_id)
        return result

    def get_positions(self, market: Optional[Market] = None) -> list[PositionSnapshot]:
        from datetime import UTC
        now = datetime.now(UTC)
        positions: list[PositionSnapshot] = []
        for pos in self._logic.open_positions:
            if market is not None and pos.market is not market:
                continue
            try:
                q = self._oracle.quote(pos.market, pos.ticker, as_of=now)
                mid = q.mid
            except Exception:
                mid = None
            if mid is None:
                pnl = None
            elif pos.side is OrderSide.BUY:
                pnl = (mid - pos.entry_price) * pos.volume
            else:
                pnl = (pos.entry_price - mid) * pos.volume
            positions.append(
                PositionSnapshot(
                    market=pos.market, ticker=pos.ticker, side=pos.side,
                    volume=pos.volume, entry_price=pos.entry_price,
                    entry_ts=pos.entry_ts, current_price=mid,
                    unrealized_pnl=pnl, sl=pos.sl, tp=pos.tp,
                )
            )
        return positions

    def get_account(self) -> AccountSnapshot:
        from datetime import UTC
        now = datetime.now(UTC)
        def _quote_safe(m, t):
            try:
                return self._oracle.quote(m, t, as_of=now)
            except Exception:
                # Missing data → treat as if position is at entry (zero unrealised)
                logic_pos = self._logic._positions.get((m, t))
                if logic_pos is None:
                    return Quote(bid=0, ask=0, last=0, ts=now)
                p = logic_pos.entry_price
                return Quote(bid=p, ask=p, last=p, ts=now)
        equity = self._logic.mark_to_market(_quote_safe)
        return AccountSnapshot(
            base_currency=self._logic.base_currency,
            balance=self._logic.cash,
            equity=equity,
        )

    # ── Persistence bridges ──
    def _persist_open(self, pos, result: ExecutionResult) -> None:
        if self._session_factory is None or self._account_id is None:
            return
        from brokers.paper_persistence import persist_open
        from sqlalchemy import select
        from core.models.paper import PaperAccount

        with self._session_factory() as session:
            account = session.get(PaperAccount, self._account_id)
            if account is None:
                return
            persist_open(session, account, pos, result, new_cash=self._logic.cash)

    def _persist_close(self, trade, result: ExecutionResult, decision_audit_id: Optional[str]) -> None:
        if self._session_factory is None or self._account_id is None:
            return
        from brokers.paper_persistence import persist_close
        from core.models.paper import PaperAccount

        with self._session_factory() as session:
            account = session.get(PaperAccount, self._account_id)
            if account is None:
                return
            persist_close(session, account, trade, result, new_cash=self._logic.cash,
                          decision_audit_id=decision_audit_id)
