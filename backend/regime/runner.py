"""DB-bound regime runner.

For each market and a given ``as_of`` date:

1. Pull the macro inputs the classifier needs (VIX level + 5d Δ,
   index history for 200d SMA, optionally yield-curve spread and
   DXY trend).
2. Call the pure classifier.
3. Upsert one row in ``market_regime``.

Markets:
- KR uses IDX_KOSPI_ECOS for the trend signal; VIX/DXY/yield-curve
  are still the US-world inputs because KR cap-market sentiment
  tracks them closely (especially in foreign-flow days).
- US uses IDX_SP500_FRED.

If a particular input is missing the classifier degrades to NEUTRAL
rather than fabricating data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date as DateType, datetime, timedelta
from typing import Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.models.prices import MacroSeries
from core.models.regime import MarketRegime
from core.types import Market
from regime.classifier import RegimeReading, classify_regime, trend_vs_sma

log = get_logger(__name__)


_INDEX_BY_MARKET = {
    Market.KR: "IDX_KOSPI_ECOS",
    Market.US: "IDX_SP500_FRED",
}


@dataclass
class RegimeRunReport:
    markets_classified: int = 0
    markets_failed: int = 0
    errors: list[str] = field(default_factory=list)


def run_regime_daily(
    session: Session,
    *,
    as_of: Optional[datetime] = None,
) -> RegimeRunReport:
    """Classify both markets for the most-recent macro date ≤ as_of and
    upsert into ``market_regime``. Idempotent — re-running over the same
    date just refreshes the row."""
    as_of = as_of or datetime.now(UTC)
    report = RegimeRunReport()
    for market in (Market.KR, Market.US):
        try:
            reading, target_date = _classify_for_market(session, market, as_of.date())
            _upsert_reading(session, market, target_date, reading)
            report.markets_classified += 1
            log.info(
                "regime.classified",
                market=market.value, date=target_date.isoformat(),
                label=reading.label.value,
                confidence=reading.confidence,
            )
        except Exception as e:
            log.exception("regime.failed", market=market.value, error=str(e))
            report.markets_failed += 1
            report.errors.append(f"{market.value}: {e}")
    return report


def _classify_for_market(
    session: Session, market: Market, as_of_date: DateType,
) -> tuple[RegimeReading, DateType]:
    """Pull inputs, run classifier. Returns (reading, date_assigned)."""
    # 1. VIX level + 5d Δ
    vix_history = _load_series(session, "VIX", as_of_date, days=10)
    vix_level = vix_history[-1] if vix_history else None
    vix_5d_delta: Optional[float] = None
    if len(vix_history) >= 6:
        vix_5d_delta = vix_history[-1] - vix_history[-6]

    # 2. Index trend vs 200d SMA
    index_code = _INDEX_BY_MARKET[market]
    index_history = _load_series(session, index_code, as_of_date, days=220)
    index_vs_sma200 = trend_vs_sma(index_history, window=200) if index_history else None

    # 3. Yield-curve spread (10Y − 2Y, US series)
    spread: Optional[float] = None
    y10 = _load_series(session, "RATE_US_10Y", as_of_date, days=3)
    y2 = _load_series(session, "RATE_US_2Y", as_of_date, days=3)
    if y10 and y2:
        spread = y10[-1] - y2[-1]

    # 4. DXY 20d % change
    dxy_history = _load_series(session, "FX_DXY", as_of_date, days=25)
    dxy_change: Optional[float] = None
    if len(dxy_history) >= 21 and dxy_history[-21] > 0:
        dxy_change = (dxy_history[-1] / dxy_history[-21] - 1.0) * 100.0

    # Anchor the row date to the most recent date we have any data for
    # (so we never write a regime row for a date with no macro inputs).
    anchor_date = as_of_date
    if vix_history or index_history:
        # Take the latest ts of the most recently-loaded series.
        anchor_date = _latest_ts_for_series(session, "VIX", as_of_date) or as_of_date

    reading = classify_regime(
        vix_level=vix_level,
        vix_5d_delta=vix_5d_delta,
        index_vs_sma200=index_vs_sma200,
        yield_curve_spread=spread,
        dxy_20d_change_pct=dxy_change,
    )
    return reading, anchor_date


def _load_series(
    session: Session, series_code: str, as_of_date: DateType, *, days: int,
) -> list[float]:
    """Return the most recent ``days`` values for ``series_code`` ≤ as_of_date,
    oldest-first."""
    since = as_of_date - timedelta(days=days + 30)   # extra buffer for weekends
    rows = list(session.execute(
        select(MacroSeries.ts, MacroSeries.value)
        .where(and_(
            MacroSeries.series_code == series_code,
            MacroSeries.ts >= since,
            MacroSeries.ts <= as_of_date,
        ))
        .order_by(MacroSeries.ts)
    ).all())
    return [float(v) for _, v in rows]


def _latest_ts_for_series(
    session: Session, series_code: str, as_of_date: DateType,
) -> Optional[DateType]:
    row = session.execute(
        select(MacroSeries.ts)
        .where(and_(
            MacroSeries.series_code == series_code,
            MacroSeries.ts <= as_of_date,
        ))
        .order_by(desc(MacroSeries.ts))
        .limit(1)
    ).first()
    return row[0] if row else None


def _upsert_reading(
    session: Session, market: Market, target_date: DateType, reading: RegimeReading,
) -> None:
    votes_payload = [
        {"signal": v.signal, "vote": v.vote, "detail": v.detail}
        for v in reading.votes
    ]
    stmt = pg_insert(MarketRegime).values(
        market=market.value,
        ts=target_date,
        label=reading.label.value,
        confidence=reading.confidence,
        votes=votes_payload,
        raw_inputs=reading.raw_inputs,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="pk_market_regime",
        set_={
            "label": stmt.excluded.label,
            "confidence": stmt.excluded.confidence,
            "votes": stmt.excluded.votes,
            "raw_inputs": stmt.excluded.raw_inputs,
        },
    )
    session.execute(stmt)
    session.flush()
