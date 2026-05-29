"""Paper-trading account, positions, and trades.

Paper trading is a first-class mode of the system: every order the
decision engine produces flows through `brokers/paper.py` (Phase 0.7)
which writes here. Comparing paper P&L vs. live P&L is one of the
drift signals in Phase 4.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base, CreatedAt, market_column


class PaperAccount(Base):
    """A virtual trading account. Multiple accounts allowed for
    A/B testing different model versions or strategy stacks."""

    __tablename__ = "paper_accounts"
    __table_args__ = (
        UniqueConstraint("name", name="uq_paper_accounts_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    base_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, doc="USD | KRW — drives reporting baseline"
    )
    initial_balance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    current_balance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = CreatedAt

    positions: Mapped[list["PaperPosition"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    trades: Mapped[list["PaperTrade"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class PaperPosition(Base):
    """Open virtual position. Closed positions move to `paper_trades`
    (we don't keep them here so the table stays small / hot)."""

    __tablename__ = "paper_positions"
    __table_args__ = (
        Index("ix_paper_positions_account_market_ticker", "account_id", "market", "ticker"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False, doc="BUY | SELL (long/short)")
    volume: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    current_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    sl: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    tp: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    entry_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text)

    account: Mapped[PaperAccount] = relationship(back_populates="positions")


class PaperTrade(Base):
    """Closed virtual trade — full lifecycle record for backtesting,
    Sharpe / drawdown reports, and live-vs-paper drift comparison."""

    __tablename__ = "paper_trades"
    __table_args__ = (
        Index("ix_paper_trades_account_close_ts", "account_id", "exit_ts"),
        Index("ix_paper_trades_market_ticker", "market", "ticker"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    volume: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    exit_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    entry_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pnl: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, doc="In the trade's symbol currency"
    )
    pnl_base_ccy: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2),
        doc="P&L converted to the account's base currency at exit_ts FX rate",
    )
    commission: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    tax: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    slippage_bps: Mapped[Optional[float]] = mapped_column(Numeric(8, 3))
    decision_audit_id: Mapped[Optional[str]] = mapped_column(
        String(36), doc="UUID of the DecisionAudit that produced this trade"
    )
    comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = CreatedAt

    account: Mapped[PaperAccount] = relationship(back_populates="trades")
