"""Daily-prices loader — orchestrate adapters + DB upsert.

`sync_daily_prices` walks the active securities universe (or a
caller-supplied subset), pulls each ticker's bars over [start, end],
and upserts into ``daily_prices``. Idempotent — re-running for the
same window updates existing rows in place.

We deliberately load one ticker per API round-trip (per market) and
flush per ticker. Bulk yfinance download is a future optimisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date as DateType, datetime
from typing import Callable, Optional

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.models.audit import DataFreshness
from core.models.prices import DailyPrice
from core.models.universe import Security
from core.types import Market
from data.price.types import DailyBar

log = get_logger(__name__)


@dataclass
class PriceSyncReport:
    market: Market
    tickers_processed: int = 0
    tickers_failed: int = 0
    rows_upserted: int = 0
    errors: list[str] = field(default_factory=list)


def sync_daily_prices(
    session: Session,
    *,
    market: Market,
    start: DateType,
    end: DateType,
    tickers: Optional[list[str]] = None,
    fetcher: Optional[Callable[[str, DateType, DateType], list[DailyBar]]] = None,
    source_label: Optional[str] = None,
    batch_size: int = 50,
) -> PriceSyncReport:
    """Fetch [start, end] OHLCV for ``tickers`` (or full active universe)
    in ``market`` and upsert into ``daily_prices``.

    Parameters
    ----------
    tickers : list[str], optional
        If omitted, every active security in ``market`` is loaded.
    fetcher : callable, optional
        Per-ticker fetcher. Defaults to KR/US production adapters.
        Tests pass a stub returning canned bars.
    source_label : str, optional
        Recorded in `data_freshness`. Inferred from market when omitted.
    batch_size : int
        How many tickers to upsert per flush. Bigger = fewer round trips,
        more memory per transaction.
    """
    report = PriceSyncReport(market=market)
    now = datetime.now(UTC)

    if tickers is None:
        tickers = _active_tickers(session, market)
    if not tickers:
        report.errors.append("no active tickers")
        _stamp(session, source_label or _default_source(market), market, "prices_daily",
               now, ok=False, error=report.errors[-1])
        return report

    if fetcher is None:
        fetcher = _default_fetcher(market)

    batch: list[DailyBar] = []
    for ticker in tickers:
        try:
            bars = fetcher(ticker, start, end)
        except Exception as e:
            log.exception("price.fetch_failed", market=market.value, ticker=ticker)
            report.tickers_failed += 1
            report.errors.append(f"{ticker}: {e}")
            continue

        report.tickers_processed += 1
        batch.extend(bars)
        if len(batch) >= batch_size * 250:  # ~batch_size tickers × 250 trading days
            report.rows_upserted += _upsert_bars(session, batch)
            batch.clear()

    if batch:
        report.rows_upserted += _upsert_bars(session, batch)

    _stamp(
        session, source_label or _default_source(market), market, "prices_daily",
        now, ok=report.tickers_failed == 0,
        rows=report.rows_upserted,
        error=None if not report.errors else f"{report.tickers_failed} ticker(s) failed",
    )
    log.info(
        "price.sync_done", market=market.value,
        processed=report.tickers_processed, failed=report.tickers_failed,
        rows=report.rows_upserted,
    )
    return report


# ──────────────────────────────────────────────────────────────────────


def _active_tickers(session: Session, market: Market) -> list[str]:
    stmt = (
        select(Security.ticker)
        .where(and_(Security.market == market.value, Security.is_active.is_(True)))
        .order_by(Security.ticker)
    )
    return [r[0] for r in session.execute(stmt)]


def _upsert_bars(session: Session, bars: list[DailyBar]) -> int:
    if not bars:
        return 0
    rows = [
        {
            "trade_date": b.trade_date,
            "market": b.market.value,
            "ticker": b.ticker,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "adj_close": b.adj_close,
            "foreign_net": b.foreign_net,
            "institution_net": b.institution_net,
            "source": b.source,
            "as_of_ts": b.as_of_ts or datetime.now(UTC),
        }
        for b in bars
    ]
    # Chunk to stay under Postgres' 65535-parameter cap: 13 cols/row → a single
    # statement caps at ~5040 rows. 4000 leaves headroom. (Large syncs — full
    # universe × long range — otherwise overflow in one INSERT.)
    CHUNK = 4000
    for k in range(0, len(rows), CHUNK):
        chunk = rows[k:k + CHUNK]
        stmt = pg_insert(DailyPrice).values(chunk)
        update_cols = {
            c.name: c
            for c in stmt.excluded
            if c.name not in {"trade_date", "market", "ticker", "created_at"}
        }
        stmt = stmt.on_conflict_do_update(constraint="pk_daily_prices", set_=update_cols)
        session.execute(stmt)
    session.flush()
    return len(rows)


def _default_source(market: Market) -> str:
    return "yfinance"   # KR now uses yfinance too (pykrx = KRX-login-walled here)


def _default_fetcher(market: Market) -> Callable:
    """Bind one of our production adapters to a per-ticker callable."""
    if market is Market.KR:
        # pykrx is KRX-login-walled in this environment (freezes KR prices);
        # yfinance (.KS/.KQ) serves fresh KR bars. Swap here to unblock KR.
        from data.price.kr_prices import build_yfinance_kr_price_fetcher, fetch_kr_daily

        adapters = build_yfinance_kr_price_fetcher()

        def _kr(ticker: str, start: DateType, end: DateType):
            return fetch_kr_daily(ticker=ticker, start=start, end=end,
                                  source="yfinance", **adapters)
        return _kr

    from data.price.us_prices import build_default_us_price_fetcher, fetch_us_daily

    adapters = build_default_us_price_fetcher()

    def _us(ticker: str, start: DateType, end: DateType):
        return fetch_us_daily(ticker=ticker, start=start, end=end, **adapters)
    return _us


def _stamp(
    session: Session, source: str, market: Market, scope: str, now: datetime,
    *, ok: bool, rows: Optional[int] = None, error: Optional[str] = None,
) -> None:
    stmt = select(DataFreshness).where(
        and_(
            DataFreshness.source == source,
            DataFreshness.market == market.value,
            DataFreshness.scope == scope,
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
