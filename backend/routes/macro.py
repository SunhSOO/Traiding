"""Macro indicator endpoints.

Three reads:

- ``GET /series`` — list of series available, with last value + last
  refresh + 30-day change.
- ``GET /series/{code}`` — full time-series for one series (capped at
  ``max_points`` evenly downsampled).
- ``GET /snapshot`` — single-row "current macro state" for the
  dashboard widget: each headline series' latest value + day/week/
  month change.

Read-only; ingestion happens via the ``macro.daily`` scheduler job."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.models.prices import MacroSeries
from core.security import CurrentUser

router = APIRouter()


# Headline series for the snapshot view — kept short so the widget
# stays readable. Operator can extend; series_code → display label.
HEADLINE_SERIES: list[tuple[str, str, str]] = [
    # (code, display_label, unit_hint)
    ("RATE_US_FFR", "美 기준금리", "%"),
    ("RATE_US_10Y", "美 10년 국채", "%"),
    ("VIX", "VIX", ""),
    ("FX_DXY", "달러 지수", ""),
    ("FX_USDKRW", "USD/KRW", "₩"),
    ("RATE_KR_BASE", "韓 기준금리", "%"),
    ("IDX_KOSPI_ECOS", "KOSPI", ""),
]


class SeriesRow(BaseModel):
    series_code: str
    label: str
    unit: str
    source: str
    latest_value: Optional[float] = None
    latest_ts: Optional[str] = None
    change_1d: Optional[float] = None
    change_1w: Optional[float] = None
    change_30d: Optional[float] = None


class SeriesPoint(BaseModel):
    ts: str        # YYYY-MM-DD
    value: float


class SeriesDetailOut(BaseModel):
    series_code: str
    label: str
    unit: str
    source: str
    points: list[SeriesPoint]


# ──────────────────────────────────────────────────────────────────────


@router.get("/snapshot", response_model=list[SeriesRow])
async def macro_snapshot(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> list[SeriesRow]:
    """Headline series only — one row each with latest + 1d/1w/30d change."""
    return await _list_series(db, codes=[c for c, _, _ in HEADLINE_SERIES])


@router.get("/series", response_model=list[SeriesRow])
async def list_series(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> list[SeriesRow]:
    """Every series present in macro_series, with the same per-row
    summary as the snapshot endpoint."""
    distinct_codes = [
        row[0] for row in (
            await db.execute(
                select(MacroSeries.series_code).distinct()
            )
        ).all()
    ]
    if not distinct_codes:
        return []
    return await _list_series(db, codes=distinct_codes)


@router.get("/series/{series_code}", response_model=SeriesDetailOut)
async def series_detail(
    series_code: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    days: int = Query(365, ge=7, le=3650),
    max_points: int = Query(500, ge=50, le=2000),
) -> SeriesDetailOut:
    since = date.today() - timedelta(days=days)
    rows = list((await db.execute(
        select(MacroSeries.ts, MacroSeries.value, MacroSeries.source)
        .where(and_(
            MacroSeries.series_code == series_code,
            MacroSeries.ts >= since,
        ))
        .order_by(MacroSeries.ts)
    )).all())
    if not rows:
        raise HTTPException(404, f"no data for {series_code}")
    points = [SeriesPoint(ts=ts.isoformat(), value=float(v)) for ts, v, _ in rows]
    if len(points) > max_points:
        points = _downsample(points, max_points)
    source = rows[0][2]
    label, unit = _series_meta(series_code)
    return SeriesDetailOut(
        series_code=series_code, label=label, unit=unit,
        source=source, points=points,
    )


# ──────────────────────────────────────────────────────────────────────


async def _list_series(db: AsyncSession, *, codes: list[str]) -> list[SeriesRow]:
    if not codes:
        return []
    today = date.today()
    out: list[SeriesRow] = []
    for code in codes:
        latest = (await db.execute(
            select(MacroSeries.ts, MacroSeries.value, MacroSeries.source,
                   MacroSeries.as_of_ts)
            .where(MacroSeries.series_code == code)
            .order_by(desc(MacroSeries.ts))
            .limit(1)
        )).first()
        if latest is None:
            label, unit = _series_meta(code)
            out.append(SeriesRow(
                series_code=code, label=label, unit=unit, source="—",
            ))
            continue
        ts, value, source, _ = latest
        change_1d = await _change_vs(db, code, ts, days=1)
        change_1w = await _change_vs(db, code, ts, days=7)
        change_30d = await _change_vs(db, code, ts, days=30)
        label, unit = _series_meta(code)
        out.append(SeriesRow(
            series_code=code, label=label, unit=unit, source=source,
            latest_value=float(value), latest_ts=ts.isoformat(),
            change_1d=change_1d, change_1w=change_1w, change_30d=change_30d,
        ))
    return out


async def _change_vs(
    db: AsyncSession, code: str, latest_ts: date, days: int,
) -> Optional[float]:
    target = latest_ts - timedelta(days=days)
    # Find the row with the largest ts ≤ target (last known value as of N days ago)
    row = (await db.execute(
        select(MacroSeries.value, MacroSeries.ts)
        .where(and_(
            MacroSeries.series_code == code,
            MacroSeries.ts <= target,
        ))
        .order_by(desc(MacroSeries.ts))
        .limit(1)
    )).first()
    if row is None:
        return None
    past, _ = row
    latest_val = (await db.execute(
        select(MacroSeries.value)
        .where(and_(
            MacroSeries.series_code == code,
            MacroSeries.ts == latest_ts,
        ))
    )).scalar()
    if latest_val is None or past == 0:
        return None
    return (float(latest_val) - float(past)) / float(past)


def _series_meta(code: str) -> tuple[str, str]:
    for c, label, unit in HEADLINE_SERIES:
        if c == code:
            return label, unit
    return code, ""


def _downsample(points: list, target: int) -> list:
    """Even-stride downsample with endpoints preserved."""
    n = len(points)
    if n <= target:
        return points
    step = n / target
    idxs = sorted({int(i * step) for i in range(target)} | {0, n - 1})
    return [points[i] for i in idxs if 0 <= i < n]
