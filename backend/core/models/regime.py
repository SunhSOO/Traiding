"""Market regime daily readings.

One row per (market, date) holding the classifier output. Stored so
the operator's regime chart + the (future) decision-engine
regime-aware threshold scaling have a stable source of truth.

Schema is deliberately small: label + confidence + JSONB for the
voter breakdown so any future classifier swap doesn't need a
migration."""
from __future__ import annotations

from datetime import date as DateType, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Numeric, PrimaryKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, market_column


class MarketRegime(Base):
    """One classifier reading per (market, date)."""

    __tablename__ = "market_regime"
    __table_args__ = (
        PrimaryKeyConstraint("market", "ts", name="pk_market_regime"),
    )

    market: Mapped[str] = market_column()
    ts: Mapped[DateType] = mapped_column(Date, nullable=False)
    label: Mapped[str] = mapped_column(
        String(16), nullable=False, doc="RISK_ON | NEUTRAL | RISK_OFF",
    )
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    votes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
