"""Company financials — tall/skinny "fact" table keyed by concept.

Why tall/skinny instead of wide columns (revenue, op_income, …)?

- KR (K-IFRS) and US (US-GAAP) report DIFFERENT line items. A wide
  table would either ignore one taxonomy or carry a sparse 200-column
  schema, both of which are bad.
- New concepts (e.g. CapEx, FCF, segment revenue) can be added by
  inserting rows, not by ALTER TABLE.
- Backtest queries select a handful of concepts at a time — a tall
  layout with a (ticker, concept, period) index is plenty fast.

`concept` is OUR canonical code, normalised across markets. See
`data/fundamental/concepts.py` for the registry and the mapping to
DART (KR) account ids / SEC us-gaap tags.

`period_end` is the fiscal-period END date; combined with
`period_kind` ('Q' or 'A') it uniquely identifies the reporting
period. ``as_of_ts`` is the publication date — the moment the
number became known to the public.
"""
from __future__ import annotations

from datetime import date as DateType, datetime
from typing import Optional

from sqlalchemy import (
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


class FinancialFact(Base):
    """One reported financial concept for one ticker for one period."""

    __tablename__ = "financial_facts"
    __table_args__ = (
        PrimaryKeyConstraint(
            "market", "ticker", "concept", "period_end", "period_kind",
            name="pk_financial_facts",
        ),
        Index("ix_financial_facts_ticker_concept_period",
              "market", "ticker", "concept", "period_end"),
        Index("ix_financial_facts_period", "period_end"),
    )

    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)

    concept: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="Canonical concept code: REVENUE, OPERATING_INCOME, "
            "NET_INCOME, TOTAL_ASSETS, ... See data/fundamental/concepts.py",
    )
    period_end: Mapped[DateType] = mapped_column(Date, nullable=False)
    period_kind: Mapped[str] = mapped_column(
        String(2), nullable=False, doc="Q | A (quarterly / annual)"
    )

    value: Mapped[float] = mapped_column(Numeric(24, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    unit_scale: Mapped[int] = mapped_column(
        Numeric(2, 0), nullable=False, default=0,
        doc="Power-of-10 multiplier applied to value, e.g. -6 if value "
            "is in millions; usually 0 because we store raw amounts.",
    )

    source: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="dart | edgar | manual"
    )
    raw_concept: Mapped[Optional[str]] = mapped_column(
        String(128),
        doc="Source's own concept name (DART account_nm or SEC us-gaap tag) — kept for audit",
    )
    as_of_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        doc="Publication timestamp — the moment this number became public",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
