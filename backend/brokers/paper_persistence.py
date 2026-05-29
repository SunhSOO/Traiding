"""Persistence layer wrapping :class:`PaperBrokerLogic`.

Lives in its own module so :mod:`brokers.paper` (pure logic) stays
importable without SQLAlchemy. Tests that need DB persistence import
from here; tests that only need the logic don't.

This module provides:

- :class:`SessionFactory` protocol — anything that can hand out a
  ``sqlalchemy.orm.Session``. The runtime passes :func:`core.db.session_scope`.
- :func:`rehydrate_account`: read all open ``paper_positions`` for an
  account back into a ``PaperBrokerLogic`` instance on startup.
- :func:`persist_open` / :func:`persist_close`: write the rows that
  match what the pure logic just did.

We split open/close into their own functions (rather than embedding
in PaperBroker.execute) so the runtime layer can compose them with
:func:`core.audit.record_decision_with_risk` inside a single
transaction.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from brokers.base import ExecutionResult
from brokers.paper import PaperBrokerLogic, _ClosedTrade, _OpenPosition
from core.logging import get_logger
from core.models.paper import PaperAccount, PaperPosition, PaperTrade
from core.types import Market

log = get_logger(__name__)


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


# ──────────────────────────────────────────────────────────────────────
# Loaders
# ──────────────────────────────────────────────────────────────────────


def load_or_create_account(
    session: Session,
    *,
    name: str,
    base_currency: str,
    initial_balance: float,
) -> PaperAccount:
    """Returns existing account by name, or creates a fresh one."""
    stmt = select(PaperAccount).where(PaperAccount.name == name)
    existing = session.scalars(stmt).first()
    if existing is not None:
        return existing
    account = PaperAccount(
        name=name,
        base_currency=base_currency,
        initial_balance=initial_balance,
        current_balance=initial_balance,
    )
    session.add(account)
    session.flush()
    log.info("paper_account.created", name=name, initial=initial_balance, ccy=base_currency)
    return account


def rehydrate_logic(session: Session, account: PaperAccount) -> PaperBrokerLogic:
    """Build a :class:`PaperBrokerLogic` from the DB row + open positions.

    Call this at process startup. The returned logic has the same
    cash + open positions the database knows about, so after a
    restart the broker reads as it did before.
    """
    logic = PaperBrokerLogic(
        base_currency=account.base_currency,
        initial_balance=float(account.initial_balance),
    )
    # Replace the broker's cash with the current balance from DB.
    object.__setattr__(logic, "_cash", float(account.current_balance))

    stmt = select(PaperPosition).where(PaperPosition.account_id == account.id)
    rows = list(session.scalars(stmt))
    for row in rows:
        pos = _OpenPosition(
            market=Market(row.market),
            ticker=row.ticker,
            side=__import__("brokers.base", fromlist=["OrderSide"]).OrderSide(row.side),
            volume=float(row.volume),
            entry_price=float(row.entry_price),
            entry_ts=row.entry_ts,
            sl=float(row.sl) if row.sl is not None else None,
            tp=float(row.tp) if row.tp is not None else None,
        )
        logic._positions[(pos.market, pos.ticker)] = pos
    log.info("paper_account.rehydrated", name=account.name, positions=len(rows), cash=float(account.current_balance))
    return logic


# ──────────────────────────────────────────────────────────────────────
# Writers — called after the pure logic mutates in-memory state
# ──────────────────────────────────────────────────────────────────────


def persist_open(
    session: Session,
    account: PaperAccount,
    pos: _OpenPosition,
    result: ExecutionResult,
    new_cash: float,
) -> PaperPosition:
    """Insert the new open position + adjust account.current_balance."""
    row = PaperPosition(
        account_id=account.id,
        market=pos.market.value,
        ticker=pos.ticker,
        side=pos.side.value,
        volume=pos.volume,
        entry_price=pos.entry_price,
        sl=pos.sl,
        tp=pos.tp,
        entry_ts=pos.entry_ts,
        comment=result.intent.comment,
    )
    session.add(row)
    account.current_balance = new_cash
    session.flush()
    log.info(
        "paper.open_persisted",
        account=account.name, market=pos.market.value, ticker=pos.ticker,
        side=pos.side.value, volume=pos.volume, entry=pos.entry_price,
    )
    return row


def persist_close(
    session: Session,
    account: PaperAccount,
    trade: _ClosedTrade,
    result: ExecutionResult,
    new_cash: float,
    decision_audit_id: Optional[str] = None,
    pnl_base_ccy: Optional[float] = None,
) -> PaperTrade:
    """Delete the open position + insert a closed trade row + adjust cash."""
    # Find and delete the open position
    from sqlalchemy import delete

    session.execute(
        delete(PaperPosition).where(
            (PaperPosition.account_id == account.id)
            & (PaperPosition.market == trade.market.value)
            & (PaperPosition.ticker == trade.ticker)
        )
    )

    row = PaperTrade(
        account_id=account.id,
        market=trade.market.value,
        ticker=trade.ticker,
        side=trade.side.value,
        volume=trade.volume,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        entry_ts=trade.entry_ts,
        exit_ts=trade.exit_ts,
        pnl=trade.pnl,
        pnl_base_ccy=pnl_base_ccy,
        commission=trade.commission,
        tax=trade.tax,
        slippage_bps=trade.slippage_bps,
        decision_audit_id=decision_audit_id,
        comment=result.intent.comment,
    )
    session.add(row)
    account.current_balance = new_cash
    session.flush()
    log.info(
        "paper.close_persisted",
        account=account.name, market=trade.market.value, ticker=trade.ticker,
        pnl=trade.pnl, new_cash=new_cash,
    )
    return row
