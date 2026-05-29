"""Backfill progress checkpoints.

One row per (job_id, source, market, ticker, period). Updated as the
historical news backfill grinds through chunks so the job is
*resumable* — if it dies mid-run, the next invocation picks up where
it left off rather than re-pulling months of already-stored articles.

This table is intentionally separate from ``data_freshness``:

- ``data_freshness`` is "what was the last time this source ran at
  all?" — used by the operator's freshness dashboard.
- ``BackfillProgress`` is the fine-grained walking cursor for a
  specific long-running historical job, with monthly resolution.

Distinct concerns; mashing them together would have made each query
awkward.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAt


class BackfillProgress(Base):
    """Resumable checkpoint for historical backfill jobs.

    ``period`` is an ISO month string (``YYYY-MM``) — months are the
    chunk unit. ``status`` ∈ {pending, in_progress, done, error}.
    ``rows_inserted`` tells the operator how productive each chunk
    was; clusters of zeros indicate dead sources for that ticker.
    """

    __tablename__ = "backfill_progress"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "source", "market", "ticker", "period",
            name="uq_backfill_chunk",
        ),
        Index("ix_backfill_job_status", "job_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False, doc="YYYY-MM")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = CreatedAt
