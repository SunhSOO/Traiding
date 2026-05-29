"""Cluster weight override endpoints.

Operator can:
- ``GET /``        — list all current overrides
- ``PUT /{cluster_id}`` — set or replace an override (audit-stamped)
- ``DELETE /{cluster_id}`` — remove an override (revert to learned)

The decision runner consults this table on startup and merges
overrides on top of the learned ``cluster_weights`` — overrides take
precedence."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.logging import get_logger
from core.models.overrides import ClusterWeightOverride
from core.security import CurrentUser

log = get_logger(__name__)
router = APIRouter()


class OverrideRow(BaseModel):
    cluster_id: str
    w_fundamental: float
    w_technical: float
    w_information: float
    reason: Optional[str] = None
    set_by: str
    set_ts: str


class OverrideUpsert(BaseModel):
    w_fundamental: float = Field(..., ge=0.0)
    w_technical: float = Field(..., ge=0.0)
    w_information: float = Field(..., ge=0.0)
    reason: Optional[str] = Field(None, max_length=500)


# ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[OverrideRow])
async def list_overrides(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> list[OverrideRow]:
    rows = (await db.execute(
        select(ClusterWeightOverride).order_by(desc(ClusterWeightOverride.set_ts))
    )).scalars()
    return [
        OverrideRow(
            cluster_id=r.cluster_id,
            w_fundamental=float(r.w_fundamental),
            w_technical=float(r.w_technical),
            w_information=float(r.w_information),
            reason=r.reason,
            set_by=r.set_by,
            set_ts=r.set_ts.isoformat(),
        )
        for r in rows
    ]


@router.put("/{cluster_id}", response_model=OverrideRow)
async def upsert_override(
    cluster_id: str,
    body: OverrideUpsert,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> OverrideRow:
    if not cluster_id:
        raise HTTPException(400, "cluster_id required")
    total = body.w_fundamental + body.w_technical + body.w_information
    if total <= 0:
        raise HTTPException(400, "weights must sum to a positive number")
    # Normalise to sum=1 so the runner doesn't need to repeat the math
    wf = body.w_fundamental / total
    wt = body.w_technical / total
    wi = body.w_information / total

    now = datetime.now(UTC)
    existing = await db.get(ClusterWeightOverride, cluster_id)
    if existing is None:
        existing = ClusterWeightOverride(
            cluster_id=cluster_id,
            w_fundamental=wf, w_technical=wt, w_information=wi,
            reason=body.reason,
            set_by=user.username if user is not None else "unknown",
            set_ts=now,
        )
        db.add(existing)
    else:
        existing.w_fundamental = wf
        existing.w_technical = wt
        existing.w_information = wi
        existing.reason = body.reason
        existing.set_by = user.username if user is not None else "unknown"
        existing.set_ts = now

    await db.flush()
    log.warning(
        "admin.cluster_override_set",
        cluster_id=cluster_id,
        weights={"F": wf, "T": wt, "I": wi},
        by=user.username if user is not None else "unknown",
    )
    return OverrideRow(
        cluster_id=cluster_id,
        w_fundamental=wf, w_technical=wt, w_information=wi,
        reason=existing.reason,
        set_by=existing.set_by,
        set_ts=existing.set_ts.isoformat(),
    )


@router.delete("/{cluster_id}")
async def delete_override(
    cluster_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> dict:
    existing = await db.get(ClusterWeightOverride, cluster_id)
    if existing is None:
        raise HTTPException(404, "no override for cluster")
    await db.delete(existing)
    log.warning(
        "admin.cluster_override_cleared",
        cluster_id=cluster_id,
        by=user.username if user is not None else "unknown",
    )
    return {"deleted": cluster_id}
