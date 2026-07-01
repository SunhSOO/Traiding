"""Integrated strategy tables — the selection→execution pipeline.

Two outputs of the SELECTION+MARKET layer (cross-sectional alpha), persisted
so the EXECUTION layer (per-stock technical timing) and the UI have a stable,
point-in-time source of truth:

* ``market_read``     — one row per (market, date): the alpha+regime market view
                        (regime, breadth, conviction) and the recommended overall
                        ``target_exposure`` (the "지수/시황" overlay; D4: basket =
                        active index, no ETF in v1).
* ``selection_basket``— one row per (market, date, ticker): the alpha's intended
                        holdings (rank, target weight, target price, bands).
                        ``in_basket`` marks the top-decile BUY candidates the
                        execution layer will time entries into.

Schema keeps a JSONB ``inputs`` so classifier/model swaps don't need migrations.
"""
from __future__ import annotations

from datetime import date as DateType, datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Numeric,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, market_column


class MarketRead(Base):
    """SELECTION+MARKET layer: the daily market view & recommended exposure."""

    __tablename__ = "market_read"
    __table_args__ = (
        PrimaryKeyConstraint("market", "as_of", name="pk_market_read"),
    )

    market: Mapped[str] = market_column()
    as_of: Mapped[DateType] = mapped_column(Date, nullable=False)
    regime: Mapped[str] = mapped_column(String(16), nullable=False, doc="RISK_ON|NEUTRAL|RISK_OFF|...")
    regime_conf: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    breadth: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0,
                                           doc="fraction of universe with positive predicted 21d return")
    avg_conviction: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False, default=0,
                                                  doc="mean predicted 21d return over the basket (signed)")
    target_exposure: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=1,
                                                   doc="recommended overall invested fraction 0..1")
    inputs: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class SelectionBasket(Base):
    """SELECTION layer: the alpha's intended holdings for (market, date)."""

    __tablename__ = "selection_basket"
    __table_args__ = (
        PrimaryKeyConstraint("market", "as_of", "ticker", name="pk_selection_basket"),
    )

    market: Mapped[str] = market_column()
    as_of: Mapped[DateType] = mapped_column(Date, nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    rank_pct: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    target_weight: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False, default=0)
    pred_ret_21d: Mapped[Optional[float]] = mapped_column(Numeric(10, 6), nullable=True)
    target_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    band_low: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    band_high: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    in_basket: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                            doc="top-decile BUY candidate")
    regime: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    market_exposure: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
