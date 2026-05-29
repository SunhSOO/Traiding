"""Fundamental loader — upsert FinancialFactRow into financial_facts.

Orchestrates KR + US adapters. Idempotent: same period re-runs simply
update existing rows (e.g. when amendments arrive). Best-effort:
individual ticker failures count toward the report but don't stop the
batch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Iterable, Optional

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.models.audit import DataFreshness
from core.models.financials import FinancialFact
from core.models.universe import Security
from core.types import Market
from data.fundamental.types import FinancialFactRow

log = get_logger(__name__)


@dataclass
class FundamentalSyncReport:
    market: Market
    tickers_processed: int = 0
    tickers_failed: int = 0
    rows_upserted: int = 0
    errors: list[str] = field(default_factory=list)


def sync_financials(
    session: Session,
    *,
    market: Market,
    tickers: Optional[list[str]] = None,
    fetcher: Optional[Callable[[str], list[FinancialFactRow]]] = None,
    source_label: Optional[str] = None,
) -> FundamentalSyncReport:
    """Pull each ticker's financials via ``fetcher(ticker)`` and upsert.

    ``fetcher`` returns a list of FinancialFactRow for a ticker (may
    span many periods). Production wires KR-specific (DART) or
    US-specific (EDGAR) fetchers — see `kr_dart.py` / `us_edgar.py`.
    """
    report = FundamentalSyncReport(market=market)
    now = datetime.now(UTC)

    if tickers is None:
        tickers = _active_tickers(session, market)
    if not tickers:
        report.errors.append("no active tickers")
        _stamp(session, source_label or _default_source(market),
               market, "financials", now, ok=False, error=report.errors[-1])
        return report

    if fetcher is None:
        report.errors.append("no fetcher provided and no production wiring yet for this market")
        _stamp(session, source_label or _default_source(market), market, "financials", now,
               ok=False, error=report.errors[-1])
        return report

    batch: list[FinancialFactRow] = []
    for ticker in tickers:
        try:
            rows = fetcher(ticker)
        except Exception as e:
            log.exception("fundamental.fetch_failed", market=market.value, ticker=ticker)
            report.tickers_failed += 1
            report.errors.append(f"{ticker}: {e}")
            continue

        report.tickers_processed += 1
        batch.extend(rows)
        if len(batch) >= 5000:
            report.rows_upserted += _upsert(session, batch)
            batch.clear()

    if batch:
        report.rows_upserted += _upsert(session, batch)

    _stamp(
        session, source_label or _default_source(market), market, "financials", now,
        ok=report.tickers_failed == 0,
        rows=report.rows_upserted,
        error=None if not report.errors else f"{report.tickers_failed} ticker(s) failed",
    )
    log.info(
        "fundamental.sync_done", market=market.value,
        processed=report.tickers_processed, failed=report.tickers_failed,
        rows=report.rows_upserted,
    )
    return report


def _upsert(session: Session, rows: Iterable[FinancialFactRow]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    payload = [
        {
            "market": r.market.value,
            "ticker": r.ticker,
            "concept": r.concept,
            "period_end": r.period_end,
            "period_kind": r.period_kind,
            "value": r.value,
            "currency": r.currency,
            "unit_scale": 0,
            "source": r.source,
            "raw_concept": r.raw_concept,
            "as_of_ts": r.as_of_ts or datetime.now(UTC),
        }
        for r in rows
    ]
    stmt = pg_insert(FinancialFact).values(payload)
    update_cols = {
        c.name: c for c in stmt.excluded
        if c.name not in {"market", "ticker", "concept", "period_end", "period_kind", "created_at"}
    }
    stmt = stmt.on_conflict_do_update(constraint="pk_financial_facts", set_=update_cols)
    session.execute(stmt)
    session.flush()
    return len(payload)


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
