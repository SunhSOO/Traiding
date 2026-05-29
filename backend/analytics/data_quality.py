"""Pure data-quality math — gap detection, freshness scoring.

No SQLAlchemy / FastAPI deps. Takes already-loaded sequences and
returns structured findings the route or scheduler can persist.

The two outputs:

- ``find_gaps`` — for one (market, ticker), given a sorted list of
  trade dates + an exchange-calendar function, return the trading
  days that *should* have a price row but don't. Returns gap windows
  rather than individual days so the operator-facing list stays short.

- ``score_freshness`` — for one ticker, given the most-recent price
  date and the current date, classify into FRESH / STALE / DEAD with
  the underlying day count. Threshold defaults are conservative and
  per-market (KR uses 3-day weekday tolerance, US 4-day to cover the
  occasional federal holiday)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterable, Literal, Optional


FreshnessTier = Literal["FRESH", "STALE", "DEAD"]


@dataclass(frozen=True)
class GapWindow:
    """A contiguous run of missing trading days for one ticker."""
    market: str
    ticker: str
    start: date          # first missing trading day
    end: date            # last missing trading day (inclusive)
    days: int            # number of trading days missing
    expected_days: int   # 1-indexed; days the calendar said should exist


@dataclass(frozen=True)
class FreshnessScore:
    market: str
    ticker: str
    last_price_date: Optional[date]
    days_since_last: Optional[int]
    tier: FreshnessTier
    threshold_stale: int     # documented for transparency
    threshold_dead: int


# Per-market staleness thresholds in *calendar* days.
# A 2-day weekend is normal; over 4 days = probably ingestion-broken;
# over 30 days = the ticker is delisted or routing changed.
FRESHNESS_THRESHOLDS: dict[str, tuple[int, int]] = {
    "KR": (4, 30),
    "US": (5, 30),
}


# ──────────────────────────────────────────────────────────────────────


def find_gaps(
    *,
    market: str,
    ticker: str,
    actual_dates: Iterable[date],
    is_trading_day: Callable[[str, date], bool],
    start: date,
    end: date,
    min_gap_days: int = 2,
) -> list[GapWindow]:
    """Walk [start, end] day-by-day; cluster missing trading days
    into windows of size ≥ ``min_gap_days``."""
    actual_set = set(actual_dates)
    windows: list[GapWindow] = []
    cur_start: Optional[date] = None
    cur_days = 0
    expected_so_far = 0

    d = start
    while d <= end:
        if not is_trading_day(market, d):
            d += timedelta(days=1)
            continue
        expected_so_far += 1
        if d in actual_set:
            if cur_start is not None and cur_days >= min_gap_days:
                windows.append(GapWindow(
                    market=market, ticker=ticker,
                    start=cur_start, end=d - timedelta(days=1),
                    days=cur_days, expected_days=expected_so_far - 1,
                ))
            cur_start = None
            cur_days = 0
        else:
            if cur_start is None:
                cur_start = d
            cur_days += 1
        d += timedelta(days=1)

    if cur_start is not None and cur_days >= min_gap_days:
        windows.append(GapWindow(
            market=market, ticker=ticker,
            start=cur_start, end=end,
            days=cur_days, expected_days=expected_so_far,
        ))
    return windows


def score_freshness(
    *,
    market: str,
    ticker: str,
    last_price_date: Optional[date],
    today: date,
) -> FreshnessScore:
    """Classify the most-recent price into FRESH / STALE / DEAD."""
    stale_th, dead_th = FRESHNESS_THRESHOLDS.get(market, (5, 30))
    if last_price_date is None:
        return FreshnessScore(
            market=market, ticker=ticker,
            last_price_date=None, days_since_last=None,
            tier="DEAD",
            threshold_stale=stale_th, threshold_dead=dead_th,
        )
    days = (today - last_price_date).days
    if days <= stale_th:
        tier: FreshnessTier = "FRESH"
    elif days <= dead_th:
        tier = "STALE"
    else:
        tier = "DEAD"
    return FreshnessScore(
        market=market, ticker=ticker,
        last_price_date=last_price_date, days_since_last=days,
        tier=tier,
        threshold_stale=stale_th, threshold_dead=dead_th,
    )


# Minimal calendar — Mon-Fri only. Real holiday calendars live in
# ``markets/kr_calendar.py`` / ``markets/us_calendar.py``; the route
# wires those in. We keep a sensible default here so tests don't
# need to import the holiday lists.
def is_weekday(market: str, d: date) -> bool:
    return d.weekday() < 5
