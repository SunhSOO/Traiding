"""Fundamental runner — load → ratios → sector percentile → score → persist.

For each ticker in a market:
1. Load latest concepts (per the look-ahead-safe `as_of`).
2. Compute ratios.
After all tickers done:
3. Group by sector, compute within-sector percentile.
4. Score each ticker.
5. Upsert into ``module_scores`` with module='F'.

The two-pass shape is necessary because percentile needs the full
sector. We do it in a single transaction so a partial run can't
leave half-scored data.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.as_of import require_as_of
from core.logging import get_logger
from core.models.financials import FinancialFact
from core.models.prices import DailyPrice
from core.models.scores import ModuleScore
from core.models.universe import Security
from core.types import Market
from fundamental.ratios import Ratios, compute_ratios
from fundamental.score import score_fundamental
from fundamental.sector_percentile import RATIO_DIRECTION, percentile_within_sector

log = get_logger(__name__)


@dataclass
class FundamentalRunReport:
    market: Market
    tickers_processed: int = 0
    tickers_skipped_no_data: int = 0
    rows_written: int = 0
    errors: list[str] = field(default_factory=list)


@require_as_of
def score_market(
    session: Session,
    *,
    market: Market,
    as_of: datetime,
    tickers: Optional[list[str]] = None,
) -> FundamentalRunReport:
    report = FundamentalRunReport(market=market)
    if tickers is None:
        tickers = _active_tickers(session, market)

    # Pass 1: compute ratios per ticker, grouped by sector.
    sector_tickers: dict[str, list[str]] = defaultdict(list)
    sector_ratios: dict[str, list[Ratios]] = defaultdict(list)
    sector_for: dict[str, str] = {}

    for ticker in tickers:
        sec = session.get(Security, (market.value, ticker))
        sector = (sec.sector if sec else None) or "_UNKNOWN_"
        sector_for[ticker] = sector

        try:
            current, prior = _load_concepts(session, market, ticker, as_of)
            price, shares = _latest_price_and_shares(session, market, ticker, as_of, current)
            ratios = compute_ratios(
                current=current, prior_year=prior,
                price=price, shares_outstanding=shares,
            )
        except Exception as e:
            log.exception("fundamental.ratios_failed", market=market.value, ticker=ticker)
            report.errors.append(f"{ticker}: {e}")
            continue

        if ratios.defined_count == 0:
            report.tickers_skipped_no_data += 1
            continue

        sector_tickers[sector].append(ticker)
        sector_ratios[sector].append(ratios)

    # Pass 2: per-sector percentile + score + persist.
    for sector, tickers_in_sec in sector_tickers.items():
        ratios_list = sector_ratios[sector]
        column_view = _column_view(ratios_list)
        percentiles = percentile_within_sector(column_view)

        for ticker, ratios, pct in zip(tickers_in_sec, ratios_list, percentiles):
            try:
                fs = score_fundamental(pct)
                report.rows_written += _persist(
                    session, market, ticker, as_of, fs, ratios, sector,
                )
                report.tickers_processed += 1
            except Exception as e:
                log.exception("fundamental.score_failed", market=market.value, ticker=ticker)
                report.errors.append(f"{ticker}: {e}")

    log.info(
        "fundamental.run_done", market=market.value,
        processed=report.tickers_processed,
        skipped=report.tickers_skipped_no_data,
        rows=report.rows_written, sectors=len(sector_tickers),
    )
    return report


# ──────────────────────────────────────────────────────────────────────


def _column_view(ratios_list: list[Ratios]) -> dict[str, list[Optional[float]]]:
    """Convert list-of-Ratios → dict of ratio_name → list."""
    return {
        name: [getattr(r, name) for r in ratios_list]
        for name in RATIO_DIRECTION  # iterate canonical ratio set
    }


def _persist(
    session: Session,
    market: Market,
    ticker: str,
    as_of: datetime,
    fs,
    ratios: Ratios,
    sector: str,
) -> int:
    payload = [{
        "computed_ts": as_of,
        "market": market.value,
        "ticker": ticker,
        "module": "F",
        "score": fs.score,
        "confidence": fs.confidence,
        "model_version": None,
        "inputs": {
            "ratios": {k: v for k, v in ratios.as_dict().items() if v is not None},
            "breakdown": fs.breakdown,
            "sector": sector,
        },
    }]
    stmt = pg_insert(ModuleScore).values(payload)
    update_cols = {
        c.name: c for c in stmt.excluded
        if c.name not in {"computed_ts", "market", "ticker", "module", "created_at"}
    }
    stmt = stmt.on_conflict_do_update(constraint="pk_module_scores", set_=update_cols)
    session.execute(stmt)
    session.flush()
    return 1


def _active_tickers(session: Session, market: Market) -> list[str]:
    stmt = (
        select(Security.ticker)
        .where(and_(Security.market == market.value, Security.is_active.is_(True)))
        .order_by(Security.ticker)
    )
    return [r[0] for r in session.execute(stmt)]


def _load_concepts(
    session: Session,
    market: Market,
    ticker: str,
    as_of: datetime,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return (current_period_concepts, prior_year_concepts) maps.

    "Current" = most-recent quarterly (or annual) period whose
    ``as_of_ts <= as_of``. "Prior year" = same period 12 months earlier
    (best-effort match by period_end month).
    """
    # Latest period (any concept, any quarter/annual) for this ticker.
    latest_stmt = (
        select(FinancialFact)
        .where(and_(
            FinancialFact.market == market.value,
            FinancialFact.ticker == ticker,
            FinancialFact.as_of_ts <= as_of,
        ))
        .order_by(desc(FinancialFact.period_end), desc(FinancialFact.as_of_ts))
        .limit(200)   # plenty of concepts per period
    )
    rows = list(session.scalars(latest_stmt))
    if not rows:
        return ({}, {})

    latest_period = rows[0].period_end
    current = {
        r.concept: float(r.value)
        for r in rows if r.period_end == latest_period
    }

    # Prior year — same calendar month, 1 year before.
    target_prior_end = latest_period.replace(year=latest_period.year - 1)
    prior = {
        r.concept: float(r.value)
        for r in rows if r.period_end == target_prior_end
    }
    if not prior:
        # Couldn't find exact YoY — try nearest period-end within ±35 days.
        candidates = [r for r in rows if abs((r.period_end - target_prior_end).days) <= 35]
        if candidates:
            chosen_end = candidates[0].period_end
            prior = {r.concept: float(r.value) for r in candidates if r.period_end == chosen_end}

    return (current, prior)


def _latest_price_and_shares(
    session: Session,
    market: Market,
    ticker: str,
    as_of: datetime,
    current_concepts: dict[str, float],
) -> tuple[Optional[float], Optional[float]]:
    """Latest close price <= as_of, plus shares from concepts."""
    stmt = (
        select(DailyPrice)
        .where(and_(
            DailyPrice.market == market.value,
            DailyPrice.ticker == ticker,
            DailyPrice.as_of_ts <= as_of,
        ))
        .order_by(desc(DailyPrice.trade_date))
        .limit(1)
    )
    row = session.scalars(stmt).first()
    price = float(row.close) if row else None
    shares = current_concepts.get("SHARES_OUTSTANDING")
    return (price, shares)
