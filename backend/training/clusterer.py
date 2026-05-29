"""Ticker clustering.

Default scheme is rule-based to keep the first iteration deterministic
and inspectable:

    cluster_id = f"{market}:{sector_bucket}:{cap_bucket}"

- ``sector_bucket`` is taken from ``Security.sector`` collapsed into
  ~10 buckets per market (e.g. "Technology", "Financials", "Energy",
  "Health Care", "Industrials", ...). Unknown sectors → "Other".
- ``cap_bucket`` is "LARGE" / "MID" / "SMALL" derived from 30-day
  median dollar volume (proxy for size; we don't always have market
  cap directly).

The result is ~15-25 distinct clusters per market — small enough that
linear regression per cluster has enough samples (300 KR tickers ×
~250 obs/ticker / ~20 clusters = ~4000 samples/cluster).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional, TYPE_CHECKING

from training.types import ClusterAssignment

if TYPE_CHECKING:
    # Heavy imports kept behind TYPE_CHECKING so the pure helpers
    # ``bucket_sector`` / ``bucket_size`` are importable without
    # SQLAlchemy. The DB-touching ``assign_clusters`` does its imports
    # lazily inside the function body.
    from sqlalchemy.orm import Session


# Canonical sector bucket map — used to collapse free-text sector
# strings into a small, stable set.
SECTOR_BUCKETS: dict[str, str] = {
    "Technology": "TECH",
    "Information Technology": "TECH",
    "Semiconductors": "TECH",
    "Communication Services": "COMM",
    "Telecommunication Services": "COMM",
    "Financials": "FIN",
    "Financial Services": "FIN",
    "Health Care": "HEALTH",
    "Healthcare": "HEALTH",
    "Consumer Discretionary": "CONS_DISC",
    "Consumer Cyclical": "CONS_DISC",
    "Consumer Staples": "CONS_STAPLES",
    "Energy": "ENERGY",
    "Industrials": "INDUSTRIALS",
    "Materials": "MATERIALS",
    "Real Estate": "REAL_ESTATE",
    "Utilities": "UTILITIES",
}


def bucket_sector(raw: Optional[str]) -> str:
    if not raw:
        return "OTHER"
    s = raw.strip()
    if s in SECTOR_BUCKETS:
        return SECTOR_BUCKETS[s]
    # Loose match — many DART / SEC sector strings have minor variants
    lower = s.lower()
    for k, v in SECTOR_BUCKETS.items():
        if k.lower() in lower or lower in k.lower():
            return v
    return "OTHER"


def bucket_size(median_dollar_volume: Optional[float], *, market: str) -> str:
    """Map median dollar volume to LARGE / MID / SMALL.

    Thresholds differ per market because absolute KRW vs USD are not
    comparable. Numbers picked to roughly tertile the KOSPI200 +
    KOSDAQ150 universe in KR and SP500 + NASDAQ-100 in US."""
    if median_dollar_volume is None or median_dollar_volume <= 0:
        return "SMALL"
    if market == "KR":
        if median_dollar_volume >= 5e10:        # 50B KRW/day
            return "LARGE"
        if median_dollar_volume >= 5e9:         # 5B KRW/day
            return "MID"
        return "SMALL"
    # US — USD-denominated
    if median_dollar_volume >= 1e8:             # $100M/day
        return "LARGE"
    if median_dollar_volume >= 1e7:             # $10M/day
        return "MID"
    return "SMALL"


def assign_clusters(
    session: "Session",
    *,
    market: str,
    as_of: datetime,
    tickers: Optional[Iterable[str]] = None,
    lookback_days: int = 30,
) -> list[ClusterAssignment]:
    """Build cluster assignments for every active ticker in ``market``.

    Each ticker gets one ClusterAssignment with audit-friendly
    ``features`` dict. The runner persists these to
    ``ticker_clusters`` with the same ``assigned_at``.

    `as_of` is honoured manually here (rather than via @require_as_of
    decorator) so this module stays importable without SQLAlchemy.
    """
    from core.as_of import _validate
    _validate(as_of)  # raises AsOfError on naive / future / None as_of

    from core.models.universe import Security  # noqa: F401 — for module side-effects
    if tickers is None:
        tickers = _active_tickers(session, market)

    from core.models.universe import Security

    out: list[ClusterAssignment] = []
    for ticker in tickers:
        sec = session.get(Security, (market, ticker))
        if sec is None:
            continue
        sector_bucket = bucket_sector(sec.sector)
        med_vol = _median_dollar_volume(session, market, ticker, as_of, lookback_days)
        size_bucket = bucket_size(med_vol, market=market)
        cluster_id = f"{market}:{sector_bucket}:{size_bucket}"
        out.append(ClusterAssignment(
            market=market, ticker=ticker, cluster_id=cluster_id,
            features={
                "raw_sector": sec.sector,
                "sector_bucket": sector_bucket,
                "size_bucket": size_bucket,
                "median_dollar_volume": med_vol,
            },
        ))
    return out


def _active_tickers(session: "Session", market: str) -> list[str]:
    from sqlalchemy import and_, select
    from core.models.universe import Security
    return [
        r[0] for r in session.execute(
            select(Security.ticker)
            .where(and_(Security.market == market, Security.is_active.is_(True)))
            .order_by(Security.ticker)
        )
    ]


def _median_dollar_volume(
    session: "Session", market: str, ticker: str,
    as_of: datetime, lookback_days: int,
) -> Optional[float]:
    """Median of (close × volume) over the last `lookback_days`."""
    from sqlalchemy import and_, desc, select
    from core.models.prices import DailyPrice

    cutoff = as_of - timedelta(days=lookback_days * 2)  # take extras for holidays
    stmt = (
        select(DailyPrice)
        .where(and_(
            DailyPrice.market == market,
            DailyPrice.ticker == ticker,
            DailyPrice.as_of_ts <= as_of,
            DailyPrice.trade_date >= cutoff.date(),
        ))
        .order_by(desc(DailyPrice.trade_date))
        .limit(lookback_days)
    )
    rows = list(session.scalars(stmt))
    if not rows:
        return None
    dollar_vols = sorted(float(r.close) * float(r.volume) for r in rows)
    mid = len(dollar_vols) // 2
    if len(dollar_vols) % 2 == 1:
        return dollar_vols[mid]
    return (dollar_vols[mid - 1] + dollar_vols[mid]) / 2.0
