"""Historical news backfill control + monitoring.

- ``POST /api/backfill/news/start`` — kicks off (or resumes) a job
- ``GET /api/backfill/{job_id}/status`` — counts pending / in-progress
  / done / errored chunks
- ``GET /api/backfill/{job_id}/errors`` — list of error rows for triage

The actual heavy lifting runs synchronously inside the request. We
cap each invocation at ``max_chunks`` so the API call has a bounded
duration; a scheduler job invokes this on a cron to chip away at
multi-month backfills over time."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db, session_scope
from core.logging import get_logger
from core.models.backfill import BackfillProgress
from core.security import CurrentUser
from core.types import Market

log = get_logger(__name__)
router = APIRouter()


class StartRequest(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=64)
    start: date
    end: date
    market: Optional[str] = Field(None, description="KR | US (omit for both)")
    sources: list[str] = Field(default_factory=lambda: ["bigkinds", "gdelt"])
    max_chunks: int = Field(50, ge=1, le=1000)


class StartResponse(BaseModel):
    job_id: str
    planned: int
    ran: int
    succeeded: int
    failed: int
    rows_inserted: int
    next_chunks_remaining: int


class StatusCounts(BaseModel):
    pending: int = 0
    in_progress: int = 0
    done: int = 0
    error: int = 0


class StatusResponse(BaseModel):
    job_id: str
    counts: StatusCounts
    total: int


class ErrorRow(BaseModel):
    source: str
    market: str
    ticker: str
    period: str
    error: str


# ──────────────────────────────────────────────────────────────────────


@router.post("/news/start", response_model=StartResponse)
async def start_news_backfill(
    body: StartRequest, user: CurrentUser, request: Request,
) -> StartResponse:
    """Plan + run up to ``max_chunks`` of a historical news backfill.

    This endpoint runs the heavy lifting synchronously. It is meant
    to be called by the scheduler (one chunk batch per invocation),
    not interactively from the UI for big jobs. The UI may invoke it
    for ad-hoc small batches (max_chunks ≤ 20)."""
    if body.end < body.start:
        raise HTTPException(400, "end before start")
    if body.market is not None:
        try:
            Market(body.market.upper())
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {body.market}") from e

    # The pure orchestrator + the news loader are sync. Use session_scope
    # so we don't tie up the async DB connection for the duration.
    from data.news.historical_runner import run_historical_backfill

    with session_scope() as session:
        summary = run_historical_backfill(
            session,
            job_id=body.job_id,
            market=body.market.upper() if body.market else None,
            sources=tuple(body.sources),
            start=body.start, end=body.end,
            max_chunks=body.max_chunks,
        )
    return StartResponse(**summary)


class MacroBackfillRequest(BaseModel):
    start: date
    end: date
    series_codes: Optional[list[str]] = Field(
        None,
        description=(
            "Restrict to specific internal codes (e.g. ['RATE_US_FFR']). "
            "Omit → use the headline set (US/KR rates, VIX, DXY, USDKRW, KOSPI)."
        ),
    )


class MacroBackfillResponse(BaseModel):
    series_processed: int
    series_failed: int
    rows_upserted: int
    window_start: str
    window_end: str
    errors: list[str]


_HEADLINE_MACRO_CODES = [
    "RATE_US_FFR", "RATE_US_10Y", "VIX", "FX_DXY",
    "FX_USDKRW", "RATE_KR_BASE", "IDX_KOSPI_ECOS",
]


@router.post("/macro/run", response_model=MacroBackfillResponse)
async def start_macro_backfill(
    body: MacroBackfillRequest, user: CurrentUser, request: Request,
) -> MacroBackfillResponse:
    """One-shot historical macro backfill.

    Pulls the requested window in one call — the loader is already
    idempotent (FRED/ECOS occasionally revise; matching values become
    no-ops). 10-year backfill of ~7 headline series ≈ a few hundred
    HTTP requests; fine to run synchronously."""
    if body.end < body.start:
        raise HTTPException(400, "end before start")

    from data.macro.loader import default_routing_fetcher, sync_macro_series

    codes = body.series_codes or _HEADLINE_MACRO_CODES
    with session_scope() as session:
        report = sync_macro_series(
            session,
            start=body.start, end=body.end,
            series_codes=codes,
            fetcher=default_routing_fetcher,
        )

    return MacroBackfillResponse(
        series_processed=report.series_processed,
        series_failed=report.series_failed,
        rows_upserted=report.rows_upserted,
        window_start=body.start.isoformat(),
        window_end=body.end.isoformat(),
        errors=report.errors,
    )


@router.get("/{job_id}/status", response_model=StatusResponse)
async def job_status(
    job_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> StatusResponse:
    rows = list((await db.execute(
        select(BackfillProgress.status, func.count())
        .where(BackfillProgress.job_id == job_id)
        .group_by(BackfillProgress.status)
    )).all())
    counts = StatusCounts()
    for status, n in rows:
        if hasattr(counts, status):
            setattr(counts, status, int(n))
    total = counts.pending + counts.in_progress + counts.done + counts.error
    return StatusResponse(job_id=job_id, counts=counts, total=total)


@router.get("/{job_id}/errors", response_model=list[ErrorRow])
async def job_errors(
    job_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    limit: int = 200,
) -> list[ErrorRow]:
    rows = (await db.execute(
        select(BackfillProgress)
        .where(and_(
            BackfillProgress.job_id == job_id,
            BackfillProgress.status == "error",
        ))
        .limit(limit)
    )).scalars()
    return [
        ErrorRow(
            source=r.source, market=r.market, ticker=r.ticker,
            period=r.period, error=r.error or "—",
        )
        for r in rows
    ]
