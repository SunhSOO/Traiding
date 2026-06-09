"""Calendar event flags — Wave 1.

For each (market, date), produce flags about proximity to known
high-impact macro events:

  - is_fomc_day, days_to_fomc, days_since_fomc
  - is_cpi_release, days_to_cpi, days_since_cpi
  - is_nfp_day (1st Friday of month, US)
  - is_pce_release (US, ~last Friday of month)
  - is_gdp_release (US, quarterly)
  - is_earnings_season (US Jan/Apr/Jul/Oct first 4 weeks)
  - days_to_quarter_end, days_to_year_end (tax window)
  - kr_bok_day (KR Bank of Korea meeting)
  - kr_holiday_next, kr_holiday_prev (Lunar New Year, Chuseok 등)

These flags exploit well-known calendar anomalies that move volatility
around scheduled events.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType, timedelta

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────────────────
# FOMC meeting dates 2023-2027 (8 per year, approximate; refresh from
# https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
# ──────────────────────────────────────────────────────────────────────
FOMC_DATES: list[DateType] = [
    # 2023
    DateType(2023, 2, 1), DateType(2023, 3, 22), DateType(2023, 5, 3),
    DateType(2023, 6, 14), DateType(2023, 7, 26), DateType(2023, 9, 20),
    DateType(2023, 11, 1), DateType(2023, 12, 13),
    # 2024
    DateType(2024, 1, 31), DateType(2024, 3, 20), DateType(2024, 5, 1),
    DateType(2024, 6, 12), DateType(2024, 7, 31), DateType(2024, 9, 18),
    DateType(2024, 11, 7), DateType(2024, 12, 18),
    # 2025
    DateType(2025, 1, 29), DateType(2025, 3, 19), DateType(2025, 5, 7),
    DateType(2025, 6, 18), DateType(2025, 7, 30), DateType(2025, 9, 17),
    DateType(2025, 10, 29), DateType(2025, 12, 10),
    # 2026 (projected)
    DateType(2026, 1, 28), DateType(2026, 3, 18), DateType(2026, 4, 29),
    DateType(2026, 6, 17), DateType(2026, 7, 29), DateType(2026, 9, 16),
    DateType(2026, 10, 28), DateType(2026, 12, 9),
    # 2027 (projected)
    DateType(2027, 1, 27), DateType(2027, 3, 17), DateType(2027, 5, 5),
    DateType(2027, 6, 16), DateType(2027, 7, 28), DateType(2027, 9, 22),
    DateType(2027, 11, 3), DateType(2027, 12, 15),
]


# BOK base rate decisions — actual dates from BOK schedule.
BOK_DATES: list[DateType] = [
    DateType(2023, 1, 13), DateType(2023, 2, 23), DateType(2023, 4, 11),
    DateType(2023, 5, 25), DateType(2023, 7, 13), DateType(2023, 8, 24),
    DateType(2023, 10, 19), DateType(2023, 11, 30),
    DateType(2024, 1, 11), DateType(2024, 2, 22), DateType(2024, 4, 12),
    DateType(2024, 5, 23), DateType(2024, 7, 11), DateType(2024, 8, 22),
    DateType(2024, 10, 11), DateType(2024, 11, 28),
    DateType(2025, 1, 16), DateType(2025, 2, 25), DateType(2025, 4, 17),
    DateType(2025, 5, 29), DateType(2025, 7, 10), DateType(2025, 8, 28),
    DateType(2025, 10, 23), DateType(2025, 11, 27),
    DateType(2026, 1, 15), DateType(2026, 2, 26), DateType(2026, 4, 16),
    DateType(2026, 5, 28), DateType(2026, 7, 9), DateType(2026, 8, 27),
    DateType(2026, 10, 22), DateType(2026, 11, 26),
]


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> DateType:
    """n-th occurrence (1-indexed) of weekday (Mon=0) in month."""
    d = DateType(year, month, 1)
    delta = (weekday - d.weekday()) % 7
    first = d + timedelta(days=delta)
    return first + timedelta(weeks=n - 1)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> DateType:
    if month == 12:
        next_month = DateType(year + 1, 1, 1)
    else:
        next_month = DateType(year, month + 1, 1)
    last = next_month - timedelta(days=1)
    while last.weekday() != weekday:
        last -= timedelta(days=1)
    return last


def _nfp_days_for_years(years: range) -> list[DateType]:
    """NFP: 1st Friday of each month."""
    out = []
    for y in years:
        for m in range(1, 13):
            out.append(_nth_weekday_of_month(y, m, 4, 1))   # Fri=4
    return out


def _cpi_days_for_years(years: range) -> list[DateType]:
    """CPI: usually ~2nd Wednesday (10-15th). We use 2nd Wed as proxy."""
    out = []
    for y in years:
        for m in range(1, 13):
            out.append(_nth_weekday_of_month(y, m, 2, 2))   # Wed=2
    return out


def _pce_days_for_years(years: range) -> list[DateType]:
    """PCE: last Friday of month (BEA release)."""
    out = []
    for y in years:
        for m in range(1, 13):
            out.append(_last_weekday_of_month(y, m, 4))
    return out


def _gdp_days_for_years(years: range) -> list[DateType]:
    """GDP advance estimate: ~last Thursday of Jan/Apr/Jul/Oct."""
    out = []
    for y in years:
        for m in (1, 4, 7, 10):
            out.append(_last_weekday_of_month(y, m, 3))    # Thu=3
    return out


@dataclass
class CalendarFlags:
    is_fomc_day: int = 0
    days_to_fomc: int = 99
    days_since_fomc: int = 99
    is_bok_day: int = 0
    days_to_bok: int = 99
    is_nfp_day: int = 0
    days_to_nfp: int = 99
    is_cpi_day: int = 0
    days_to_cpi: int = 99
    is_pce_day: int = 0
    is_gdp_day: int = 0
    is_earnings_season: int = 0
    days_to_quarter_end: int = 99
    days_to_year_end: int = 99


def compute_event_calendar_features(dates, market: str) -> pd.DataFrame:
    """Compute calendar event flags for given date list/index.

    Accepts list[date] or pd.DatetimeIndex. Returns DataFrame indexed by
    the original input dates (matching pd.Timestamp normalize).
    """
    if hasattr(dates, "to_pydatetime"):
        date_list = [d.date() if hasattr(d, "date") else d for d in dates]
    else:
        date_list = list(dates)
    years = range(min(d.year for d in date_list) - 1, max(d.year for d in date_list) + 2)
    nfp_set = set(_nfp_days_for_years(years))
    cpi_set = set(_cpi_days_for_years(years))
    pce_set = set(_pce_days_for_years(years))
    gdp_set = set(_gdp_days_for_years(years))
    fomc_set = set(FOMC_DATES)
    bok_set = set(BOK_DATES)

    fomc_sorted = sorted(FOMC_DATES)
    bok_sorted = sorted(BOK_DATES)
    nfp_sorted = sorted(nfp_set)
    cpi_sorted = sorted(cpi_set)

    rows = []
    for d in date_list:
        flags = CalendarFlags()
        # FOMC
        if d in fomc_set:
            flags.is_fomc_day = 1
        flags.days_to_fomc = min(
            ((f - d).days for f in fomc_sorted if (f - d).days >= 0),
            default=99,
        )
        flags.days_since_fomc = min(
            ((d - f).days for f in fomc_sorted if (d - f).days >= 0),
            default=99,
        )
        # BOK (only relevant for KR)
        if market == "KR":
            if d in bok_set:
                flags.is_bok_day = 1
            flags.days_to_bok = min(
                ((b - d).days for b in bok_sorted if (b - d).days >= 0),
                default=99,
            )
        # NFP
        if d in nfp_set:
            flags.is_nfp_day = 1
        flags.days_to_nfp = min(
            ((n - d).days for n in nfp_sorted if (n - d).days >= 0),
            default=99,
        )
        # CPI
        if d in cpi_set:
            flags.is_cpi_day = 1
        flags.days_to_cpi = min(
            ((c - d).days for c in cpi_sorted if (c - d).days >= 0),
            default=99,
        )
        # PCE/GDP
        if d in pce_set:
            flags.is_pce_day = 1
        if d in gdp_set:
            flags.is_gdp_day = 1
        # Earnings season — Jan/Apr/Jul/Oct first 4 weeks
        if d.month in (1, 4, 7, 10) and d.day <= 28:
            flags.is_earnings_season = 1
        # Quarter-end / year-end distances
        q_end_month = ((d.month - 1) // 3 + 1) * 3
        if q_end_month == 12:
            q_end = DateType(d.year, 12, 31)
        elif q_end_month == 3:
            q_end = DateType(d.year, 3, 31)
        elif q_end_month == 6:
            q_end = DateType(d.year, 6, 30)
        else:
            q_end = DateType(d.year, 9, 30)
        flags.days_to_quarter_end = max(0, min(99, (q_end - d).days))
        flags.days_to_year_end = max(0, min(366, (DateType(d.year, 12, 31) - d).days))

        rows.append({
            "ts": d,
            "is_fomc_day": flags.is_fomc_day,
            "days_to_fomc": flags.days_to_fomc,
            "days_since_fomc": flags.days_since_fomc,
            "is_bok_day": flags.is_bok_day,
            "days_to_bok": flags.days_to_bok,
            "is_nfp_day": flags.is_nfp_day,
            "days_to_nfp": flags.days_to_nfp,
            "is_cpi_day": flags.is_cpi_day,
            "days_to_cpi": flags.days_to_cpi,
            "is_pce_day": flags.is_pce_day,
            "is_gdp_day": flags.is_gdp_day,
            "is_earnings_season": flags.is_earnings_season,
            "days_to_quarter_end": flags.days_to_quarter_end,
            "days_to_year_end": flags.days_to_year_end,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"])
        df = df.set_index("ts")
    return df
