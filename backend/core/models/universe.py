"""Universe tables: securities master, exchange calendars, FX rates.

These are the "ground truth" tables that almost every other table
references. Updates here trigger downstream re-processing.

`securities` includes delisted tickers (`is_active=False`) so
historical backtests don't suffer from survivorship bias.
"""
from __future__ import annotations

from datetime import date as DateType, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAt, UpdatedAt, market_column


class Security(Base):
    """Universe master. One row per (market, ticker), historical
    (delisted) tickers retained for survivorship-bias-free backtests."""

    __tablename__ = "securities"
    __table_args__ = (
        PrimaryKeyConstraint("market", "ticker", name="pk_securities"),
        Index("ix_securities_active_market", "is_active", "market"),
        Index("ix_securities_sector", "market", "sector"),
    )

    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    name_en: Mapped[Optional[str]] = mapped_column(String(256))
    isin: Mapped[Optional[str]] = mapped_column(String(12), index=True)
    cik: Mapped[Optional[str]] = mapped_column(
        String(10), index=True, doc="SEC CIK for US tickers"
    )
    corp_code: Mapped[Optional[str]] = mapped_column(
        String(8), index=True, doc="DART corp_code for KR tickers"
    )
    exchange: Mapped[Optional[str]] = mapped_column(
        String(16), doc="KOSPI / KOSDAQ / NYSE / NASDAQ"
    )
    index_membership: Mapped[Optional[str]] = mapped_column(
        String(64),
        doc="Comma-separated index codes the ticker belongs to "
            "(e.g. 'KOSPI200', 'SP500,NASDAQ100').",
    )
    sector: Mapped[Optional[str]] = mapped_column(String(128))
    industry: Mapped[Optional[str]] = mapped_column(String(128))
    listed_date: Mapped[Optional[DateType]] = mapped_column(Date)
    delisted_date: Mapped[Optional[DateType]] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, doc="KRW / USD")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Security {self.market}:{self.ticker} {self.name!r}>"


class ExchangeCalendar(Base):
    """Per-market trading-day cache. Populated from exchange_calendars
    library on startup and refreshed monthly. Phase 0.8 (as_of guard)
    relies on this to translate wall-clock to "last completed bar"."""

    __tablename__ = "exchange_calendars"
    __table_args__ = (
        PrimaryKeyConstraint("market", "session_date", name="pk_exchange_calendars"),
    )

    market: Mapped[str] = market_column()
    session_date: Mapped[DateType] = mapped_column(Date, nullable=False)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, nullable=False)
    session_open_local = mapped_column(Time, nullable=True, doc="local tz exchange open")
    session_close_local = mapped_column(Time, nullable=True, doc="local tz exchange close")
    open_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    close_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FxRate(Base):
    """Daily FX rates. We always store the explicit pair (no inverse
    inference), so KRW/USD and USD/KRW both end up as separate rows
    if both are pulled — simpler audit, no precision loss."""

    __tablename__ = "fx_rates"
    __table_args__ = (
        PrimaryKeyConstraint("rate_date", "base_currency", "quote_currency", name="pk_fx_rates"),
    )

    rate_date: Mapped[DateType] = mapped_column(Date, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="bok | fred | yfinance | manual"
    )
    as_of_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="When this rate first became available for use (typically T+0 close + lag).",
    )
    created_at: Mapped[datetime] = CreatedAt
