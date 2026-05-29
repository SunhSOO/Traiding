"""DB-bound wrapper for the historical news backfill.

Glues the pure :mod:`historical_backfill` orchestrator to:

- Real adapter implementations (BIGKinds, GDELT, Naver, RSS)
- The existing news loader's persistence path (via ``sync_news``-style
  upsert internals, repackaged to take pre-fetched rows)
- BackfillProgress checkpoint reads/writes
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Iterable, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.models.backfill import BackfillProgress
from core.models.universe import Security
from data.news.bigkinds import fetch_bigkinds
from data.news.gdelt import fetch_gdelt
from data.news.historical_backfill import (
    BackfillChunk, BackfillProgressRow, DEFAULT_RATE_LIMITS,
    RateLimiter, TickerSpec, plan_chunks, run_chunks,
)
from data.news.loader import _build_name_index, _upsert_chunk, _upsert_mentions
from data.news.types import NewsArticleRow

log = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────


def run_historical_backfill(
    session: Session,
    *,
    job_id: str,
    market: Optional[str] = None,
    # Default sources: only those that actually support time-windowed
    # historical queries. Naver Search has no date filter and would
    # return the same recent results for every monthly chunk, so it
    # belongs on the live ingestion path, not here.
    sources: tuple[str, ...] = ("bigkinds", "gdelt"),
    start: date,
    end: date,
    max_chunks: Optional[int] = None,
    rate_limits: Optional[dict[str, float]] = None,
) -> dict:
    """Entry point. Plans the work, runs as many chunks as fit in
    ``max_chunks``, returns a summary the route can serialise.

    Idempotent across invocations — chunks marked ``done`` in
    ``BackfillProgress`` are skipped.
    """
    tickers = _load_tickers(session, market=market)
    existing = _load_existing_progress(session, job_id=job_id)
    chunks = plan_chunks(
        job_id=job_id, tickers=tickers, sources=sources,
        start=start, end=end, existing=existing,
    )
    if not chunks:
        return {
            "job_id": job_id, "planned": 0, "ran": 0,
            "succeeded": 0, "failed": 0, "rows_inserted": 0,
            "next_chunks_remaining": 0,
        }

    name_index = _build_name_index(session)
    adapters = _build_adapters()

    def persist(chunk: BackfillChunk, rows: list[NewsArticleRow]) -> int:
        # Skip if zero rows
        if not rows:
            return 0
        inserted_count, mentions = _upsert_chunk(session, rows, name_index)
        _upsert_mentions(session, mentions)
        return inserted_count

    def update_status(chunk: BackfillChunk, status: str, inserted: int, error: Optional[str]):
        _stamp_progress(session, chunk, status, inserted, error)

    limiter = RateLimiter(min_interval_seconds=rate_limits or DEFAULT_RATE_LIMITS)
    results = run_chunks(
        chunks,
        adapters=adapters,
        persist=persist,
        update_status=update_status,
        limiter=limiter,
        max_chunks=max_chunks,
    )

    return {
        "job_id": job_id,
        "planned": len(chunks),
        "ran": len(results),
        "succeeded": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
        "rows_inserted": sum(r.rows_inserted for r in results),
        "next_chunks_remaining": max(len(chunks) - len(results), 0),
    }


# ──────────────────────────────────────────────────────────────────────


def _load_tickers(session: Session, *, market: Optional[str]) -> list[TickerSpec]:
    stmt = select(Security).where(Security.is_active.is_(True))
    if market is not None:
        stmt = stmt.where(Security.market == market)
    return [
        TickerSpec(market=row.market, ticker=row.ticker, query=row.name)
        for row in session.scalars(stmt)
        if row.name
    ]


def _load_existing_progress(session: Session, *, job_id: str) -> list[BackfillProgressRow]:
    rows = session.scalars(
        select(BackfillProgress).where(BackfillProgress.job_id == job_id)
    )
    return [
        BackfillProgressRow(
            source=r.source, market=r.market, ticker=r.ticker,
            period=r.period, status=r.status, rows_inserted=r.rows_inserted,
        )
        for r in rows
    ]


def _stamp_progress(
    session: Session, chunk: BackfillChunk, status: str,
    inserted: int, error: Optional[str],
) -> None:
    existing = session.scalars(
        select(BackfillProgress).where(and_(
            BackfillProgress.job_id == chunk.job_id,
            BackfillProgress.source == chunk.source,
            BackfillProgress.market == chunk.market,
            BackfillProgress.ticker == chunk.ticker,
            BackfillProgress.period == chunk.period,
        ))
    ).first()
    now = datetime.now(UTC)
    if existing is None:
        existing = BackfillProgress(
            job_id=chunk.job_id, source=chunk.source,
            market=chunk.market, ticker=chunk.ticker, period=chunk.period,
            status=status, rows_inserted=inserted, error=error,
        )
        session.add(existing)
    else:
        existing.status = status
        existing.rows_inserted = inserted
        existing.error = error
    if status == "in_progress":
        existing.started_ts = now
    elif status in ("done", "error"):
        existing.finished_ts = now
    session.flush()


def _build_adapters():
    """Return a {source_name: callable(chunk) -> list[NewsArticleRow]} map.

    Per-adapter knobs (page sizes, language filters) come from the
    chunk's market: KR tickers prefer Korean-language queries on
    GDELT, etc."""

    def bigkinds_fn(chunk: BackfillChunk):
        # BIGKinds only carries Korean publishers — skip for US tickers.
        if chunk.market != "KR":
            return []
        return fetch_bigkinds(
            query=chunk.query,
            start=chunk.start.date(), end=chunk.end.date(),
        )

    def gdelt_fn(chunk: BackfillChunk):
        langs = ["Korean"] if chunk.market == "KR" else ["English"]
        return fetch_gdelt(
            query=chunk.query, start=chunk.start, end=chunk.end,
            languages=langs,
        )

    return {
        "bigkinds": bigkinds_fn,
        "gdelt": gdelt_fn,
    }
