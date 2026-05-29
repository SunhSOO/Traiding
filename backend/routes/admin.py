"""Operator emergency controls.

Two responsibilities:

1. **Scheduler pause / resume** — flip every job's next-run-time on
   or off. Used when the operator wants to freeze automated activity
   without rebooting the process.

2. **Kill switch** — the single button that, in an emergency, both
   pauses the scheduler AND closes every open paper position
   immediately at market. Logs the event prominently in
   ``DecisionAudit`` so the post-mortem is straightforward.

The kill switch deliberately does NOT require the
``RequireLiveUser`` flag — in an emergency, ANY authenticated user
should be able to slam the brakes. Enabling live mode goes the
other way (live = RequireLiveUser) so the friction is asymmetric:
shutting down is easy, turning on is hard.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.logging import get_logger
from core.models.paper import PaperAccount, PaperPosition
from core.security import CurrentUser
from core.types import Market

log = get_logger(__name__)
router = APIRouter()


# ── Response shapes ──
class SchedulerState(BaseModel):
    started: bool
    paused: bool
    job_count: int
    jobs: list[dict]


class KillSwitchResult(BaseModel):
    triggered_at: str
    triggered_by: str
    scheduler_paused: bool
    scheduler_jobs_paused: int
    positions_closed: int
    close_errors: list[str]


class PauseResumeResult(BaseModel):
    paused: bool
    jobs_touched: int


# ──────────────────────────────────────────────────────────────────────


@router.get("/scheduler/state", response_model=SchedulerState)
async def scheduler_state(user: CurrentUser, request: Request) -> SchedulerState:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return SchedulerState(started=False, paused=False, job_count=0, jobs=[])
    jobs = scheduler.status()
    return SchedulerState(
        started=True,
        paused=scheduler.is_paused(),
        job_count=len(jobs),
        jobs=jobs,
    )


@router.post("/scheduler/pause", response_model=PauseResumeResult)
async def pause_scheduler(user: CurrentUser, request: Request) -> PauseResumeResult:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(503, "scheduler not running")
    count = scheduler.pause_all()
    log.warning("admin.scheduler_paused", by=user.username, jobs=count)
    return PauseResumeResult(paused=True, jobs_touched=count)


@router.post("/scheduler/resume", response_model=PauseResumeResult)
async def resume_scheduler(user: CurrentUser, request: Request) -> PauseResumeResult:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(503, "scheduler not running")
    count = scheduler.resume_all()
    log.info("admin.scheduler_resumed", by=user.username, jobs=count)
    return PauseResumeResult(paused=False, jobs_touched=count)


@router.post("/kill-switch", response_model=KillSwitchResult)
async def kill_switch(
    user: CurrentUser,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    account_name: Optional[str] = None,
) -> KillSwitchResult:
    """Emergency stop: pause scheduler + close every paper position.

    ``account_name`` omitted → close positions across ALL paper accounts
    (KR + US). Supplied → target only that account. Either way the
    scheduler is paused first so no new orders can fire during cleanup.
    Returns a structured report; per-position failures land in
    ``close_errors`` so partial success is still actionable."""
    now = datetime.now(UTC)
    log.warning(
        "admin.kill_switch_triggered",
        by=user.username, account=account_name or "ALL",
    )

    # 1. Pause scheduler first so nothing new fires during close-all.
    scheduler = getattr(request.app.state, "scheduler", None)
    jobs_paused = scheduler.pause_all() if scheduler is not None else 0

    # 2. Resolve target accounts
    if account_name:
        accounts = (await db.execute(
            select(PaperAccount).where(PaperAccount.name == account_name)
        )).scalars().all()
    else:
        accounts = (await db.execute(select(PaperAccount))).scalars().all()
    accounts = list(accounts)
    if not accounts:
        log.warning(
            "admin.kill_switch_no_account", requested=account_name or "ALL",
        )

    # 3. Close every open paper position across resolved accounts.
    from brokers.db_price_oracle import DBPriceOracle
    from brokers.paper import PaperBroker
    from brokers.paper_persistence import rehydrate_logic
    from core.db import session_scope

    closed = 0
    errors: list[str] = []
    oracle = DBPriceOracle(session_factory=session_scope)

    for account in accounts:
        try:
            open_positions = list((await db.execute(
                select(PaperPosition).where(PaperPosition.account_id == account.id)
            )).scalars())
            if not open_positions:
                continue

            # Build a broker per account. Cheap (~2 DB reads), no shared
            # state across the async loop.
            with session_scope() as setup_session:
                sync_account = setup_session.scalars(
                    select(PaperAccount).where(PaperAccount.id == account.id)
                ).first()
                logic = rehydrate_logic(setup_session, sync_account)

            broker = PaperBroker(
                logic, oracle,
                session_factory=session_scope, account_id=account.id,
            )

            for pos in open_positions:
                try:
                    market = Market(pos.market)
                    result = broker.close_position(
                        market, pos.ticker, comment="KILL_SWITCH",
                    )
                    if result.ok:
                        closed += 1
                    else:
                        errors.append(f"{account.name}/{pos.market}:{pos.ticker}: {result.error}")
                except Exception as e:
                    errors.append(f"{account.name}/{pos.market}:{pos.ticker}: {e}")
        except Exception as e:
            log.exception("admin.kill_switch_close_failed", account=account.name)
            errors.append(f"close-all {account.name} failed: {e}")

    log.warning(
        "admin.kill_switch_done",
        by=user.username, jobs_paused=jobs_paused,
        positions_closed=closed, errors=len(errors),
    )
    return KillSwitchResult(
        triggered_at=now.isoformat(),
        triggered_by=user.username,
        scheduler_paused=jobs_paused > 0,
        scheduler_jobs_paused=jobs_paused,
        positions_closed=closed,
        close_errors=errors,
    )
