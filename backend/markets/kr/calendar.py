"""KRX trading-day calendar.

Uses `exchange_calendars.get_calendar("XKRX")` as the source of truth.
We wrap it in tiny functions so the rest of the codebase never imports
exchange_calendars directly — making it trivial to swap to a different
calendar provider later (e.g. pykrx) without touching callers.

KRX is open 09:00–15:30 KST on business days, with a 30-minute
closing auction at 15:20–15:30 (we treat the full session as one
block — intraday session segmentation lives in higher-level code).
"""
from __future__ import annotations

from datetime import date as DateType, datetime, time, timedelta
from functools import lru_cache

# tz / xcals are loaded lazily — see _krx_tz() and _calendar(). Two reasons:
# 1. On Windows, zoneinfo needs the `tzdata` package; failing at import
#    time would block ALL of markets/ from being importable.
# 2. exchange_calendars pulls in pandas (~150 MB resident); we don't
#    want that cost just to read a ticker validator.

SESSION_OPEN_LOCAL = time(9, 0)
SESSION_CLOSE_LOCAL = time(15, 30)


@lru_cache(maxsize=1)
def _krx_tz():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Asia/Seoul")


# Backward compatibility: KRX_TZ accessor as a function-like attribute.
# Avoids the import-time crash on tz-data-less environments.
def KRX_TZ():  # noqa: N802 — kept uppercase for call-site readability
    return _krx_tz()


@lru_cache(maxsize=1)
def _calendar():
    """Lazy import of exchange_calendars."""
    import exchange_calendars as xcals

    return xcals.get_calendar("XKRX")


def is_trading_day(d: DateType) -> bool:
    """Whether `d` (KST date) is a KRX trading day."""
    return bool(_calendar().is_session(d.isoformat()))


def previous_trading_day(d: DateType) -> DateType:
    """The latest trading day strictly before ``d``.

    Robust to non-session inputs: ``d`` may be a weekend/holiday (e.g. the
    KRX year-end closure), in which case we walk back to the nearest prior
    session. ``exchange_calendars.previous_session`` itself requires ``d`` to
    be a session, so we only take that fast path when it is one."""
    cal = _calendar()
    if cal.is_session(d.isoformat()):
        return cal.previous_session(d.isoformat()).date()
    nd = d - timedelta(days=1)
    while not cal.is_session(nd.isoformat()):
        nd -= timedelta(days=1)
    return nd


def next_trading_day(d: DateType) -> DateType:
    """The earliest trading day strictly after ``d`` (robust to non-sessions)."""
    cal = _calendar()
    if cal.is_session(d.isoformat()):
        return cal.next_session(d.isoformat()).date()
    nd = d + timedelta(days=1)
    while not cal.is_session(nd.isoformat()):
        nd += timedelta(days=1)
    return nd


def session_open_close_utc(d: DateType) -> tuple[datetime, datetime] | None:
    """Returns timezone-aware (open_utc, close_utc) for `d`, or None
    if `d` is not a trading day. Useful for as_of bookkeeping."""
    if not is_trading_day(d):
        return None
    from zoneinfo import ZoneInfo

    tz = _krx_tz()
    open_local = datetime.combine(d, SESSION_OPEN_LOCAL, tzinfo=tz)
    close_local = datetime.combine(d, SESSION_CLOSE_LOCAL, tzinfo=tz)
    return (open_local.astimezone(ZoneInfo("UTC")), close_local.astimezone(ZoneInfo("UTC")))


def trading_days_between(start: DateType, end: DateType) -> list[DateType]:
    """Inclusive of both endpoints if they are trading days. Useful
    for backtest loop construction."""
    cal = _calendar()
    sessions = cal.sessions_in_range(start.isoformat(), end.isoformat())
    return [s.date() for s in sessions]
