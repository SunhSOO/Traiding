"""System config inspector — read-only.

Single endpoint that aggregates the configuration the operator can't
easily piece together from individual endpoints:

- DecisionConfig defaults (composite weights, thresholds, gates, sizer)
- Counts of learned cluster weights + operator overrides
- Runtime mode + app env + configured LLM providers
- Scheduler enabled-job catalog
- Database connectivity confirmation

This endpoint deliberately doesn't accept WRITE operations. Mutation
goes through the dedicated routes (``/overrides``, etc.) so the audit
trail is preserved.
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.db import get_async_db
from core.models.overrides import ClusterWeightOverride
from core.models.training import ClusterWeights
from core.security import CurrentUser
from decision.types import DecisionConfig

router = APIRouter()


class DecisionConfigView(BaseModel):
    weight_fundamental: float
    weight_technical: float
    weight_information: float
    buy_threshold: float
    sell_threshold: float
    min_module_confidence: float
    min_overall_confidence: float
    churn_minutes: int
    score_staleness_hours: int
    base_position_fraction: float
    target_volatility_bps: float
    max_position_fraction: float


class WeightStats(BaseModel):
    learned_clusters: int
    operator_overrides: int


class SchedulerJobView(BaseModel):
    id: str
    next_run: Optional[str] = None


class SchedulerView(BaseModel):
    started: bool
    paused: bool
    job_count: int
    jobs: list[SchedulerJobView]


class SystemConfigOut(BaseModel):
    runtime_mode: str
    app_env: str
    configured_llm_providers: list[str]
    db_connected: bool
    decision: DecisionConfigView
    weights: WeightStats
    scheduler: SchedulerView


# ──────────────────────────────────────────────────────────────────────


@router.get("/system", response_model=SystemConfigOut)
async def system_config(
    user: CurrentUser,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> SystemConfigOut:
    settings = get_settings()
    cfg = DecisionConfig()

    # Cluster weight counts
    learned = (await db.execute(
        select(func.count(func.distinct(ClusterWeights.cluster_id)))
    )).scalar() or 0
    overrides = (await db.execute(
        select(func.count()).select_from(ClusterWeightOverride)
    )).scalar() or 0

    # Scheduler
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        sched_view = SchedulerView(started=False, paused=False, job_count=0, jobs=[])
    else:
        jobs = scheduler.status()
        sched_view = SchedulerView(
            started=True,
            paused=scheduler.is_paused(),
            job_count=len(jobs),
            jobs=[
                SchedulerJobView(id=j["id"], next_run=j.get("next_run"))
                for j in jobs
            ],
        )

    return SystemConfigOut(
        runtime_mode=settings.runtime_mode.value,
        app_env=settings.app_env.value,
        configured_llm_providers=settings.configured_llm_providers,
        db_connected=True,    # we got here, so the DB is up
        decision=DecisionConfigView(
            weight_fundamental=cfg.weight_fundamental,
            weight_technical=cfg.weight_technical,
            weight_information=cfg.weight_information,
            buy_threshold=cfg.buy_threshold,
            sell_threshold=cfg.sell_threshold,
            min_module_confidence=cfg.min_module_confidence,
            min_overall_confidence=cfg.min_overall_confidence,
            churn_minutes=cfg.churn_minutes,
            score_staleness_hours=cfg.score_staleness_hours,
            base_position_fraction=cfg.base_position_fraction,
            target_volatility_bps=cfg.target_volatility_bps,
            max_position_fraction=cfg.max_position_fraction,
        ),
        weights=WeightStats(
            learned_clusters=int(learned),
            operator_overrides=int(overrides),
        ),
        scheduler=sched_view,
    )
