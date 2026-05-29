"""Universe loader — upsert securities + maintain membership history.

The single public entry point is :func:`sync_universe`. Given a
session and (optionally) per-market fetcher dicts, it:

1. Pulls fresh `SecurityInfo` from each market's adapters.
2. Merges into the existing ``securities`` rows (UPSERT).
3. Updates ``universe_membership``:
   - For each (market, ticker, index_code) present in the fetched
     snapshot AND NOT in the existing open-ended membership rows,
     insert a new row with ``valid_from = as_of_date`` and
     ``valid_to = NULL``.
   - For each open-ended membership row NOT present in the snapshot,
     close it by setting ``valid_to = as_of_date``.
4. Records ``data_freshness`` for `securities` and `universe_membership`.

This pattern preserves survivorship-free history: even if a ticker
leaves KOSPI 200 tomorrow, we still know it was in KOSPI 200 last
quarter.

All writes go through a single transaction. Either everything lands
or nothing does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date as DateType, datetime
from typing import Callable, Optional

from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.models.audit import DataFreshness
from core.models.universe import Security
from core.models.prices import UniverseMembership
from core.types import Market
from data.universe.types import SecurityInfo

log = get_logger(__name__)

KrFetchers = dict[str, Callable]
UsFetchers = dict[str, Callable]


@dataclass
class SyncReport:
    market: Market
    fetched_count: int = 0
    securities_inserted: int = 0
    securities_updated: int = 0
    memberships_opened: int = 0
    memberships_closed: int = 0
    error: Optional[str] = None


def sync_universe(
    session: Session,
    *,
    as_of_date: DateType,
    kr_fetchers: Optional[KrFetchers] = None,
    us_fetchers: Optional[UsFetchers] = None,
    source_label_kr: str = "pykrx",
    source_label_us: str = "fdr",
    enrich_identifiers: bool = True,
) -> list[SyncReport]:
    """Refresh KR + US universe in a single transaction.

    Pass ``kr_fetchers=None`` (or ``us_fetchers=None``) to skip a
    market. Otherwise pass the dict produced by
    ``build_default_kr_fetcher()`` / ``build_default_us_fetcher()``
    (or a mocked dict in tests).

    ``enrich_identifiers``: when True (default), also populate
    securities.corp_code (KR via DART) and securities.cik (US via SEC)
    so downstream Phase 1 ingestion jobs can resolve tickers to
    regulator-specific identifiers.
    """
    reports: list[SyncReport] = []
    now = datetime.now(UTC)

    if kr_fetchers is not None:
        from data.universe.kr_universe import fetch_kr_universe

        kr_report = _sync_for_market(
            session, Market.KR,
            fetch_callable=lambda: fetch_kr_universe(as_of_date, **kr_fetchers),
            source_label=source_label_kr, as_of_date=as_of_date, now=now,
        )
        reports.append(kr_report)

        if enrich_identifiers:
            try:
                from data.universe.kr_enrichment import (
                    apply_corp_codes_to_securities, fetch_dart_corp_codes,
                )
                corp_map = fetch_dart_corp_codes()
                if corp_map:
                    apply_corp_codes_to_securities(session, corp_map)
            except Exception as e:
                log.warning("universe.kr_enrichment_failed", error=str(e))

    if us_fetchers is not None:
        from data.universe.us_universe import fetch_us_universe

        us_report = _sync_for_market(
            session, Market.US,
            fetch_callable=lambda: fetch_us_universe(as_of_date, **us_fetchers),
            source_label=source_label_us, as_of_date=as_of_date, now=now,
        )
        reports.append(us_report)

        if enrich_identifiers:
            try:
                from data.universe.us_enrichment import (
                    apply_ciks_to_securities, fetch_sec_ticker_cik_map,
                )
                cik_map = fetch_sec_ticker_cik_map()
                if cik_map:
                    apply_ciks_to_securities(session, cik_map)
            except Exception as e:
                log.warning("universe.us_enrichment_failed", error=str(e))

    return reports


# ──────────────────────────────────────────────────────────────────────


def _sync_for_market(
    session: Session,
    market: Market,
    *,
    fetch_callable: Callable[[], list[SecurityInfo]],
    source_label: str,
    as_of_date: DateType,
    now: datetime,
) -> SyncReport:
    report = SyncReport(market=market)
    try:
        infos = fetch_callable()
    except Exception as e:
        log.exception("universe.fetch_failed", market=market.value)
        report.error = str(e)
        _stamp_freshness(session, source_label, market, "universe", now, ok=False, error=str(e))
        return report

    report.fetched_count = len(infos)
    if not infos:
        report.error = "fetch returned zero rows"
        _stamp_freshness(session, source_label, market, "universe", now, ok=False, error=report.error)
        return report

    # 1. UPSERT securities
    inserted, updated = _upsert_securities(session, infos, source_label)
    report.securities_inserted = inserted
    report.securities_updated = updated

    # 2. Reconcile membership
    opened, closed = _reconcile_membership(
        session, market, infos, source_label, as_of_date, now,
    )
    report.memberships_opened = opened
    report.memberships_closed = closed

    _stamp_freshness(
        session, source_label, market, "universe", now,
        ok=True, rows=report.fetched_count, error=None,
    )
    log.info(
        "universe.sync_done",
        market=market.value, fetched=report.fetched_count,
        inserted=inserted, updated=updated,
        memberships_opened=opened, memberships_closed=closed,
    )
    return report


def _upsert_securities(
    session: Session,
    infos: list[SecurityInfo],
    source_label: str,
) -> tuple[int, int]:
    """PostgreSQL-specific upsert. Returns (inserted, updated)."""
    if not infos:
        return (0, 0)

    market = infos[0].market.value
    rows = [
        {
            "market": market,
            "ticker": info.ticker,
            "name": info.name,
            "name_en": info.name_en,
            "isin": info.isin,
            "cik": info.cik,
            "corp_code": info.corp_code,
            "exchange": info.exchange,
            "index_membership": ",".join(sorted(info.index_codes)) if info.index_codes else None,
            "sector": info.sector,
            "industry": info.industry,
            "listed_date": info.listed_date,
            "delisted_date": info.delisted_date,
            "is_active": info.delisted_date is None,
            "currency": info.currency,
        }
        for info in infos
    ]

    # Track existing rows to count insert vs update.
    existing_keys = set(
        session.execute(
            select(Security.market, Security.ticker).where(Security.market == market)
        )
    )

    stmt = pg_insert(Security).values(rows)
    update_cols = {
        c.name: c
        for c in stmt.excluded
        if c.name not in {"market", "ticker", "created_at"}
    }
    stmt = stmt.on_conflict_do_update(
        constraint="pk_securities", set_=update_cols
    )
    session.execute(stmt)
    session.flush()

    inserted = sum(1 for info in infos if (market, info.ticker) not in existing_keys)
    updated = len(infos) - inserted
    return (inserted, updated)


def _reconcile_membership(
    session: Session,
    market: Market,
    infos: list[SecurityInfo],
    source_label: str,
    as_of_date: DateType,
    now: datetime,
) -> tuple[int, int]:
    """Open new (market, ticker, index_code) rows; close ones no longer present."""
    fetched: set[tuple[str, str]] = set()  # (ticker, index_code)
    for info in infos:
        for idx in info.index_codes:
            fetched.add((info.ticker, idx))

    # Currently-open memberships for this market
    open_rows = list(session.scalars(
        select(UniverseMembership).where(
            and_(
                UniverseMembership.market == market.value,
                UniverseMembership.valid_to.is_(None),
            )
        )
    ))
    open_keys: set[tuple[str, str]] = {(r.ticker, r.index_code) for r in open_rows}

    # Open new ones
    new_keys = fetched - open_keys
    for ticker, index_code in new_keys:
        session.add(UniverseMembership(
            market=market.value,
            ticker=ticker,
            index_code=index_code,
            valid_from=as_of_date,
            valid_to=None,
            source=source_label,
            as_of_ts=now,
        ))

    # Close removed ones
    closed = 0
    for row in open_rows:
        if (row.ticker, row.index_code) not in fetched:
            row.valid_to = as_of_date
            closed += 1
    session.flush()
    return (len(new_keys), closed)


def _stamp_freshness(
    session: Session,
    source: str,
    market: Market,
    scope: str,
    now: datetime,
    *,
    ok: bool,
    rows: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    """Upsert one row in ``data_freshness`` to record this sync's outcome."""
    stmt = (
        select(DataFreshness)
        .where(
            and_(
                DataFreshness.source == source,
                DataFreshness.market == market.value,
                DataFreshness.scope == scope,
            )
        )
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
