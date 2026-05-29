"""NYSE / NASDAQ trading-day calendar.

NYSE and NASDAQ share the same trading-day calendar (same holidays,
same 09:30-16:00 ET regular hours), so we use the consolidated NYSE
calendar from `exchange_calendars` (`XNYS`).

Pre/after-market sessions are deliberately excluded — the system
operates on regular-hours bars only. Adding extended-hours support
is a separate decision (it impacts liquidity assumptions, slippage,
and tax/sec-fee treatment).
"""
from __future__ import annotations

from datetime import date as DateType, datetime, time
from functools import lru_cache

# See markets/kr/calendar.py for the rationale behind lazy ZoneInfo /
# exchange_calendars loading.

SESSION_OPEN_LOCAL = time(9, 30)
SESSION_CLOSE_LOCAL = time(16, 0)


@lru_cache(maxsize=1)
def _us_tz():
    from zoneinfo import ZoneInfo

    return ZoneInfo("America/New_York")


def US_TZ():  # noqa: N802
    return _us_tz()


@lru_cache(maxsize=1)
def _calendar():
    import exchange_calendars as xcals

    return xcals.get_calendar("XNYS")


def is_trading_day(d: DateType) -> bool:
    return bool(_calendar().is_session(d.isoformat()))


def previous_trading_day(d: DateType) -> DateType:
    return _calendar().previous_session(d.isoformat()).date()


def next_trading_day(d: DateType) -> DateType:
    return _calendar().next_session(d.isoformat()).date()


def session_open_close_utc(d: DateType) -> tuple[datetime, datetime] | None:
    if not is_trading_day(d):
        return None
    # Use exchange_calendars' actual open/close for the date, which
    # correctly handles early-close days (Black Friday, Christmas Eve, etc.).
    cal = _calendar()
    open_ts = cal.session_first_minute(d.isoformat())
    close_ts = cal.session_last_minute(d.isoformat())
    # exchange_calendars returns tz-aware UTC pandas.Timestamp objects.
    return (open_ts.to_pydatetime(), close_ts.to_pydatetime())


def trading_days_between(start: DateType, end: DateType) -> list[DateType]:
    sessions = _calendar().sessions_in_range(start.isoformat(), end.isoformat())
    return [s.date() for s in sessions]
