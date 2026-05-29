"""Price tables and universe-membership history.

`daily_prices` is the bedrock of every backtest, every technical
indicator, every learning label. It is also the largest table this
system maintains by row count — ~870 tickers × ~250 trading days/year
× 10+ years easily passes 2M rows. We therefore convert it into a
TimescaleDB hypertable partitioned on ``trade_date`` so range queries
(the dominant access pattern) stay fast.

`universe_membership` records when each ticker entered / left each
index. Without this, backtests would suffer survivorship bias —
"S&P 500 today" is NOT the same set as "S&P 500 in 2020".

`macro_series` is a thin generic table for FX rates, interest rates,
volatility indices, etc. Keyed by (series_code, ts).
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, market_column


class DailyPrice(Base):
    """End-of-day OHLCV bar for one (market, ticker, trade_date).

    `adj_close` reflects splits / dividends per source. We always
    store `close` raw and `adj_close` adjusted so consumers can pick.
    `as_of_ts` is the moment this row became known to us (close + ingestion lag);
    backtest queries filter on `as_of_ts <= as_of` for look-ahead-bias-free
    point-in-time correctness.
    """

    __tablename__ = "daily_prices"
    __table_args__ = (
        # trade_date first in PK so hypertable partitioning aligns with it.
        PrimaryKeyConstraint("trade_date", "market", "ticker", name="pk_daily_prices"),
        Index("ix_daily_prices_ticker_date", "market", "ticker", "trade_date"),
    )

    trade_date: Mapped[DateType] = mapped_column(Date, nullable=False)
    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)

    open: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[int] = mapped_column(Numeric(20, 0), nullable=False)
    adj_close: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))

    # KR-only: 외국인/기관 순매수 (KRW). Nullable for US rows.
    foreign_net: Mapped[Optional[float]] = mapped_column(Numeric(20, 0))
    institution_net: Mapped[Optional[float]] = mapped_column(Numeric(20, 0))

    source: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="pykrx | yfinance | fdr | manual"
    )
    as_of_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="When this row first became known. Used by as_of-filtered reads.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniverseMembership(Base):
    """Index-constituent history.

    Append-only. One row per (market, ticker, index_code, valid_from).
    `valid_to` is NULL while the ticker is currently in the index;
    when it drops out, set `valid_to` to the trading day on which it
    left. This lets us reconstruct "the KOSPI 200 as of 2023-05-04"
    with a single range predicate.
    """

    __tablename__ = "universe_membership"
    __table_args__ = (
        PrimaryKeyConstraint("market", "ticker", "index_code", "valid_from",
                             name="pk_universe_membership"),
        Index("ix_universe_membership_index_active", "index_code", "valid_to"),
    )

    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    index_code: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="KOSPI200 | KOSDAQ150 | SP500 | NASDAQ100",
    )
    valid_from: Mapped[DateType] = mapped_column(Date, nullable=False)
    valid_to: Mapped[Optional[DateType]] = mapped_column(Date)

    weight: Mapped[Optional[float]] = mapped_column(
        Numeric(8, 6), doc="Index weight if known, else NULL"
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MacroSeries(Base):
    """Generic macro time series — FX rates, central-bank rates,
    indices (VIX, KOSPI, S&P 500), commodities.

    Keyed by (series_code, ts). `series_code` follows our internal
    naming, not the source's — e.g. "FX_USDKRW", "RATE_US_FFR",
    "IDX_KOSPI", "VIX". A registry of series codes lives in
    docs/generated/macro-series.md.
    """

    __tablename__ = "macro_series"
    __table_args__ = (
        PrimaryKeyConstraint("series_code", "ts", name="pk_macro_series"),
        Index("ix_macro_series_code", "series_code"),
    )

    series_code: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[DateType] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
