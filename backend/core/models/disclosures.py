"""Corporate disclosures / regulatory filings.

Holds the metadata for every filing (DART, KIND, EDGAR) plus, when
available, the cleaned body text used by the information-analysis
module's LLM classifier.

Filings are append-only — corrections (amendments) appear as new
rows referencing the original via ``amends_id``. Both KR and US
markets routinely file amendments and we want both versions on
record for audit.
"""
from __future__ import annotations

import uuid
from datetime import date as DateType, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, market_column


class Disclosure(Base):
    """One regulatory filing.

    For DART, ``source_id`` is the rcept_no (접수번호).
    For EDGAR, ``source_id`` is the accession_number.
    Unique on (source, source_id) so re-running the loader cannot
    duplicate.
    """

    __tablename__ = "disclosures"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_disclosures_source"),
        Index("ix_disclosures_market_ticker_date", "market", "ticker", "filing_date"),
        Index("ix_disclosures_filing_type", "market", "filing_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, doc="dart | kind | edgar"
    )
    source_id: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="rcept_no (DART) or accession_number (EDGAR)",
    )

    filing_date: Mapped[DateType] = mapped_column(Date, nullable=False)
    filing_ts: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        doc="Exact submission timestamp from the regulator (when known)",
    )

    filing_type: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="Source's filing-type code: '사업보고서', '주요사항보고서', "
            "'10-K', '8-K', etc.",
    )
    filing_type_canonical: Mapped[Optional[str]] = mapped_column(
        String(32),
        doc="Our normalised category: ANNUAL | QUARTERLY | MATERIAL_EVENT | "
            "INSIDER | OTHER",
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    body_text: Mapped[Optional[str]] = mapped_column(
        Text,
        doc="Cleaned plain-text body when fetched. NULL until the body "
            "fetcher runs (separate job — body fetch is rate-limited and "
            "happens lazily for filings the info module wants to classify).",
    )
    body_fetched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    amends_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("disclosures.id"),
        doc="Set when this filing amends a prior one",
    )

    as_of_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        doc="When this filing first became public — typically equals filing_ts + ingestion lag",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
