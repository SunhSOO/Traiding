"""Disclosures loader — insert DisclosureRow into `disclosures`.

UNIQUE on (source, source_id) means we use ``INSERT … ON CONFLICT DO
NOTHING`` instead of DO UPDATE. The filing's authoritative content
doesn't change after submission — amendments are new filings with
their own source_id. Re-runs of the loader for the same window are
therefore cheap no-ops.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Iterable, Optional

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.models.audit import DataFreshness
from core.models.disclosures import Disclosure
from core.models.universe import Security
from core.types import Market
from data.disclosures.types import DisclosureRow

log = get_logger(__name__)


@dataclass
class DisclosureSyncReport:
    market: Market
    tickers_processed: int = 0
    tickers_failed: int = 0
    rows_inserted: int = 0
    rows_skipped_existing: int = 0
    errors: list[str] = field(default_factory=list)


def sync_disclosures(
    session: Session,
    *,
    market: Market,
    tickers: Optional[list[str]] = None,
    fetcher: Optional[Callable[[str], list[DisclosureRow]]] = None,
    source_label: Optional[str] = None,
) -> DisclosureSyncReport:
    """For each ticker, run ``fetcher(ticker)`` and insert returned rows.

    Idempotent: ``ON CONFLICT DO NOTHING`` on (source, source_id).
    """
    report = DisclosureSyncReport(market=market)
    now = datetime.now(UTC)

    if tickers is None:
        tickers = _active_tickers(session, market)
    if not tickers:
        report.errors.append("no active tickers")
        _stamp(session, source_label or _default_source(market),
               market, "disclosures", now, ok=False, error=report.errors[-1])
        return report

    if fetcher is None:
        report.errors.append("no fetcher provided")
        _stamp(session, source_label or _default_source(market), market, "disclosures", now,
               ok=False, error=report.errors[-1])
        return report

    batch: list[DisclosureRow] = []
    for ticker in tickers:
        try:
            rows = fetcher(ticker)
        except Exception as e:
            log.exception("disclosures.fetch_failed", market=market.value, ticker=ticker)
            report.tickers_failed += 1
            report.errors.append(f"{ticker}: {e}")
            continue
        report.tickers_processed += 1
        batch.extend(rows)
        if len(batch) >= 1000:
            inserted = _insert(session, batch)
            report.rows_inserted += inserted
            report.rows_skipped_existing += len(batch) - inserted
            batch.clear()

    if batch:
        inserted = _insert(session, batch)
        report.rows_inserted += inserted
        report.rows_skipped_existing += len(batch) - inserted

    _stamp(
        session, source_label or _default_source(market), market, "disclosures", now,
        ok=report.tickers_failed == 0,
        rows=report.rows_inserted,
        error=None if not report.errors else f"{report.tickers_failed} ticker(s) failed",
    )
    log.info(
        "disclosures.sync_done", market=market.value,
        processed=report.tickers_processed, failed=report.tickers_failed,
        inserted=report.rows_inserted, skipped=report.rows_skipped_existing,
    )
    return report


def _insert(session: Session, rows: Iterable[DisclosureRow]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    payload = [
        {
            "id": uuid.uuid4(),
            "market": r.market.value,
            "ticker": r.ticker,
            "source": r.source,
            "source_id": r.source_id,
            "filing_date": r.filing_date,
            "filing_ts": r.filing_ts,
            "filing_type": r.filing_type,
            "filing_type_canonical": r.filing_type_canonical,
            "title": r.title,
            "source_url": r.source_url,
            "body_text": None,
            "body_fetched": False,
            "as_of_ts": r.as_of_ts or datetime.now(UTC),
        }
        for r in rows
    ]
    stmt = pg_insert(Disclosure).values(payload).on_conflict_do_nothing(
        constraint="uq_disclosures_source"
    )
    result = session.execute(stmt)
    session.flush()
    # rowcount is the count of rows actually inserted (post-conflict).
    return result.rowcount if result.rowcount is not None and result.rowcount >= 0 else len(payload)


def _active_tickers(session: Session, market: Market) -> list[str]:
    stmt = (
        select(Security.ticker)
        .where(and_(Security.market == market.value, Security.is_active.is_(True)))
        .order_by(Security.ticker)
    )
    return [r[0] for r in session.execute(stmt)]


def _default_source(market: Market) -> str:
    return "dart" if market is Market.KR else "edgar"


def _stamp(
    session: Session, source: str, market: Market, scope: str, now: datetime,
    *, ok: bool, rows: Optional[int] = None, error: Optional[str] = None,
) -> None:
    stmt = select(DataFreshness).where(
        and_(DataFreshness.source == source, DataFreshness.market == market.value,
             DataFreshness.scope == scope)
    )
    existing = session.scalars(stmt).first()
    if existing is None:
        existing = DataFreshness(source=source, market=market.value, scope=scope)
        session.add(existing)
    existing.last_attempt_ts = now
    if ok:
        existing.last_success_ts = now
        existing.last_error = None
        existing.rows_last_run = rows
    else:
        existing.last_error = error
    session.flush()
