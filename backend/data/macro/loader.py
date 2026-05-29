"""Macro-series loader — orchestrate FRED + ECOS fetches and upsert
into ``macro_series`` with conflict-free idempotence.

Designed to be called from the daily scheduler with the same series
list every day; existing rows are updated only if the new value
differs (FRED occasionally revises historical points)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date as DateType, datetime
from typing import Callable, Optional

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.models.audit import DataFreshness
from core.models.prices import MacroSeries
from data.macro.types import MacroPoint

log = get_logger(__name__)


@dataclass
class MacroSyncReport:
    series_processed: int = 0
    series_failed: int = 0
    rows_upserted: int = 0
    errors: list[str] = field(default_factory=list)


def sync_macro_series(
    session: Session,
    *,
    start: DateType,
    end: DateType,
    series_codes: list[str],
    fetcher: Callable[[str, DateType, DateType], list[MacroPoint]],
    source_label: str = "macro_mixed",
) -> MacroSyncReport:
    """Pull each series in ``series_codes`` and upsert.

    ``fetcher`` is a single callable that dispatches by series code
    (typically a function that consults FRED vs ECOS routing). Tests
    pass a closure that returns canned points.
    """
    report = MacroSyncReport()
    now = datetime.now(UTC)

    all_points: list[MacroPoint] = []
    for code in series_codes:
        try:
            points = fetcher(code, start, end)
        except Exception as e:
            log.exception("macro.fetch_failed", series=code)
            report.series_failed += 1
            report.errors.append(f"{code}: {e}")
            continue
        report.series_processed += 1
        all_points.extend(points)

    report.rows_upserted = _upsert(session, all_points)

    _stamp(
        session, source_label, "macro", now,
        ok=report.series_failed == 0,
        rows=report.rows_upserted,
        error=None if not report.errors else f"{report.series_failed} series failed",
    )
    log.info(
        "macro.sync_done",
        processed=report.series_processed, failed=report.series_failed,
        rows=report.rows_upserted,
    )
    return report


def _upsert(session: Session, points: list[MacroPoint]) -> int:
    if not points:
        return 0
    rows = [
        {
            "series_code": p.series_code,
            "ts": p.ts,
            "value": p.value,
            "source": p.source,
            "as_of_ts": p.as_of_ts or datetime.now(UTC),
        }
        for p in points
    ]
    stmt = pg_insert(MacroSeries).values(rows)
    update_cols = {
        c.name: c
        for c in stmt.excluded
        if c.name not in {"series_code", "ts", "created_at"}
    }
    stmt = stmt.on_conflict_do_update(constraint="pk_macro_series", set_=update_cols)
    session.execute(stmt)
    session.flush()
    return len(rows)


def _stamp(
    session: Session, source: str, scope: str, now: datetime,
    *, ok: bool, rows: Optional[int] = None, error: Optional[str] = None,
) -> None:
    stmt = select(DataFreshness).where(
        and_(DataFreshness.source == source, DataFreshness.scope == scope, DataFreshness.market.is_(None))
    )
    existing = session.scalars(stmt).first()
    if existing is None:
        existing = DataFreshness(source=source, market=None, scope=scope)
        session.add(existing)
    existing.last_attempt_ts = now
    if ok:
        existing.last_success_ts = now
        existing.last_error = None
        existing.rows_last_run = rows
    else:
        existing.last_error = error
    session.flush()


# ──────────────────────────────────────────────────────────────────────
# Default routing fetcher
# ──────────────────────────────────────────────────────────────────────


def default_routing_fetcher(code: str, start: DateType, end: DateType) -> list[MacroPoint]:
    """Routes internal codes to FRED or ECOS. Used by production
    scheduler jobs. Tests should not call this — they pass their own."""
    from data.macro.bok_ecos import CODE_MAP as ECOS_CODES, fetch_ecos_series
    from data.macro.fred import CODE_MAP as FRED_CODES, fetch_fred_series

    if code in ECOS_CODES:
        return fetch_ecos_series(code, start=start, end=end)
    if code in FRED_CODES:
        return fetch_fred_series(code, start=start, end=end)
    raise KeyError(f"no adapter knows about macro series code: {code}")
