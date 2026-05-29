"""Ingestion endpoints — data freshness + manual job triggers.

Useful both as an ops dashboard for the local operator and as a way
to backfill / catch up after a planned outage. Manual triggers
require an authenticated user; live-mode is NOT additionally required
because triggering a data refresh has no live-trading side effects.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.logging import get_logger
from core.models.audit import DataFreshness
from core.security import CurrentUser

log = get_logger(__name__)
router = APIRouter()


class FreshnessOut(BaseModel):
    source: str
    market: str | None
    scope: str
    last_success_ts: str | None
    last_attempt_ts: str | None
    last_error: str | None
    rows_last_run: int | None


class JobStatusOut(BaseModel):
    id: str
    next_run: str | None
    trigger: str | None = None


class JobTriggerResponse(BaseModel):
    job_id: str
    status: str


@router.get("/freshness", response_model=list[FreshnessOut])
async def freshness(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> list[FreshnessOut]:
    result = await db.execute(select(DataFreshness).order_by(DataFreshness.source, DataFreshness.market))
    return [
        FreshnessOut(
            source=r.source, market=r.market, scope=r.scope,
            last_success_ts=r.last_success_ts.isoformat() if r.last_success_ts else None,
            last_attempt_ts=r.last_attempt_ts.isoformat() if r.last_attempt_ts else None,
            last_error=r.last_error, rows_last_run=r.rows_last_run,
        )
        for r in result.scalars()
    ]


@router.get("/jobs", response_model=list[JobStatusOut])
async def jobs(user: CurrentUser, request: Request) -> list[JobStatusOut]:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return []
    return [JobStatusOut(**row) for row in scheduler.status()]


@router.post("/jobs/{job_id}/run", response_model=JobTriggerResponse)
async def run_job(
    job_id: str,
    user: CurrentUser,
    request: Request,
) -> JobTriggerResponse:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(503, "scheduler not running")
    try:
        await scheduler.run_job_now(job_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:
        log.exception("ingestion.manual_run_failed", job=job_id)
        raise HTTPException(500, f"job {job_id} failed: {e}") from e
    return JobTriggerResponse(job_id=job_id, status="ok")
