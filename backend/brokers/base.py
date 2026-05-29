"""BrokerAdapter abstract base.

Every venue (paper, MT5, Korea Investment Securities, Alpaca, ...) is
exposed via this same contract so the decision/runtime layer doesn't
branch on broker type. Each broker is also responsible for emitting
realistic costs (commission / tax / slippage) — the risk engine and
sizer trust whatever the broker reports.

This interface intentionally separates "intent" from "execution
result". Higher layers produce an :class:`OrderIntent`, hand it to
the broker, and receive an :class:`ExecutionResult`. The intent is
recorded in the decision audit log BEFORE the broker is called, so
even rejected/failed orders leave an audit trail.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from core.types import Market


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"   # not yet supported by PaperBroker


@dataclass(frozen=True)
class OrderIntent:
    """A trade the decision engine wants to make. Broker-agnostic.

    `volume` is in shares for equity, in lots for FX. The market
    adapter knows which is appropriate.
    """

    market: Market
    ticker: str
    side: OrderSide
    order_type: OrderType
    volume: float
    limit_price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    comment: str = ""
    decision_audit_id: Optional[str] = None


@dataclass(frozen=True)
class ExecutionResult:
    """What actually happened when the broker tried to execute.

    `ok=False` is a normal outcome (e.g. risk-rejected, market closed,
    insufficient funds) and is fully captured here; the caller decides
    whether to retry, alert, or skip.
    """

    ok: bool
    intent: OrderIntent
    fill_price: Optional[float] = None
    fill_volume: Optional[float] = None
    fill_ts: Optional[datetime] = None
    commission: float = 0.0
    tax: float = 0.0
    slippage_bps: Optional[float] = None
    venue_order_id: Optional[str] = None
    error: Optional[str] = None
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PositionSnapshot:
    """Read-only view of an open position. Brokers return these from
    :meth:`BrokerAdapter.get_positions`."""

    market: Market
    ticker: str
    side: OrderSide
    volume: float
    entry_price: float
    entry_ts: datetime
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None


@dataclass(frozen=True)
class AccountSnapshot:
    base_currency: str
    balance: float
    equity: float
    margin_used: float = 0.0
    margin_free: Optional[float] = None


class BrokerAdapter(ABC):
    """The contract every broker must satisfy."""

    name: str

    @abstractmethod
    def execute(self, intent: OrderIntent) -> ExecutionResult:
        ...

    @abstractmethod
    def close_position(
        self,
        market: Market,
        ticker: str,
        *,
        comment: str = "",
        decision_audit_id: Optional[str] = None,
    ) -> ExecutionResult:
        ...

    @abstractmethod
    def get_positions(self, market: Optional[Market] = None) -> list[PositionSnapshot]:
        ...

    @abstractmethod
    def get_account(self) -> AccountSnapshot:
        ...
