"""BOK ECOS adapter — Bank of Korea Economic Statistics System.

API docs: https://ecos.bok.or.kr/api/  (Korean; English docs sparse).
Quota: free, generous. Requires `BOK_ECOS_API_KEY`.

URL shape:
  http://ecos.bok.or.kr/api/StatisticSearch/{KEY}/{format}/{lang}/
       {start_count}/{end_count}/{stat_code}/{cycle}/{start_date}/
       {end_date}/{item_code1}

`cycle` is one of D / M / Q / A. We use D for FX, M/A for most rates.

Internal → ECOS (stat_code, item_code, cycle) map. Items can be
multi-level; we use the leaf-level item codes only.
"""
from __future__ import annotations

from datetime import UTC, date as DateType, datetime
from typing import Callable, Optional

import httpx

from core.config import get_settings
from core.logging import get_logger
from data.macro.types import MacroPoint

log = get_logger(__name__)

ECOS_BASE = "http://ecos.bok.or.kr/api"

# Internal code → (stat_code, item_code, cycle).
# References:
#   FX_USDKRW: '731Y001' daily closing rate, item 0000001 (USD)
#   RATE_KR_BASE: '722Y001' Bank of Korea Base Rate (monthly)
CODE_MAP: dict[str, tuple[str, str, str]] = {
    "FX_USDKRW": ("731Y001", "0000001", "D"),
    "FX_JPYKRW": ("731Y001", "0000002", "D"),   # JPY per KRW (per 100 JPY)
    "RATE_KR_BASE": ("722Y001", "0101000", "M"),
    "IDX_KOSPI_ECOS": ("802Y001", "0001000", "D"),   # KOSPI daily close
}


def fetch_ecos_series(
    series_code: str,
    *,
    start: DateType,
    end: DateType,
    api_key: Optional[str] = None,
    http_get: Callable[[str, dict | None], httpx.Response] | None = None,
) -> list[MacroPoint]:
    if series_code not in CODE_MAP:
        raise KeyError(f"unknown ECOS series code: {series_code}")
    stat_code, item_code, cycle = CODE_MAP[series_code]
    settings = get_settings()
    key = api_key or settings.bok_ecos_api_key.get_secret_value()
    if not key:
        log.warning("ecos.no_api_key")
        return []

    sd, ed = _fmt_dates(start, end, cycle)
    url = (
        f"{ECOS_BASE}/StatisticSearch/{key}/json/kr/1/10000/"
        f"{stat_code}/{cycle}/{sd}/{ed}/{item_code}"
    )
    getter = http_get or (lambda u, p: httpx.get(u, timeout=30))
    try:
        resp = getter(url, None)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("ecos.fetch_failed", series=series_code, error=str(e))
        return []

    # ECOS wraps results in {"StatisticSearch": {"row": [...]}}.
    rows = (data.get("StatisticSearch") or {}).get("row", []) if isinstance(data, dict) else []
    now = datetime.now(UTC)
    out: list[MacroPoint] = []
    for r in rows:
        raw_time = r.get("TIME")
        raw_value = r.get("DATA_VALUE")
        d = _parse_ecos_time(raw_time, cycle)
        if d is None or raw_value in (None, ""):
            continue
        try:
            v = float(raw_value)
        except ValueError:
            continue
        out.append(MacroPoint(
            series_code=series_code, ts=d, value=v, source="bok_ecos", as_of_ts=now,
        ))
    return out


def _fmt_dates(start: DateType, end: DateType, cycle: str) -> tuple[str, str]:
    if cycle == "D":
        return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    if cycle == "M":
        return start.strftime("%Y%m"), end.strftime("%Y%m")
    if cycle == "Q":
        return f"{start.year}Q{(start.month - 1) // 3 + 1}", f"{end.year}Q{(end.month - 1) // 3 + 1}"
    return start.strftime("%Y"), end.strftime("%Y")


def _parse_ecos_time(raw: Optional[str], cycle: str) -> Optional[DateType]:
    if not raw:
        return None
    try:
        if cycle == "D":
            return datetime.strptime(raw, "%Y%m%d").date()
        if cycle == "M":
            return datetime.strptime(raw + "01", "%Y%m%d").date()
        if cycle == "Q":
            # raw like '2024Q1' -> first day of quarter
            yr = int(raw[:4])
            q = int(raw[-1])
            return DateType(yr, (q - 1) * 3 + 1, 1)
        return datetime.strptime(raw, "%Y").date()
    except ValueError:
        return None
