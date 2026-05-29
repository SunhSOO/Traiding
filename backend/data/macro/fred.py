"""FRED adapter — St. Louis Fed economic data API.

API docs: https://fred.stlouisfed.org/docs/api/fred/
Quota: free, generous (120 req / 60s). Requires `FRED_API_KEY`.

Internal → FRED series_id map. Add to this table as new series are
needed; never hardcode the FRED id at call sites.
"""
from __future__ import annotations

from datetime import UTC, date as DateType, datetime
from typing import Callable, Optional

import httpx

from core.config import get_settings
from core.logging import get_logger
from data.macro.types import MacroPoint

log = get_logger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred"

# Internal series-code → FRED series ID.
CODE_MAP: dict[str, str] = {
    "RATE_US_FFR": "DFF",            # Effective Federal Funds Rate
    "RATE_US_10Y": "DGS10",          # 10-Year Treasury yield
    "RATE_US_2Y": "DGS2",
    "VIX": "VIXCLS",                 # CBOE Volatility Index
    "FX_DXY": "DTWEXBGS",            # USD index (broad)
    "FX_USDKRW_FRED": "DEXKOUS",     # USD/KRW (fallback to BOK primary)
    "IDX_SP500_FRED": "SP500",       # daily S&P 500 close
}


def fetch_fred_series(
    series_code: str,
    *,
    start: DateType,
    end: DateType,
    api_key: Optional[str] = None,
    http_get: Callable[[str, dict], httpx.Response] | None = None,
) -> list[MacroPoint]:
    """Fetch one FRED series in [start, end] → list of normalised points.

    Parameters
    ----------
    series_code : str
        Internal code (must be present in :data:`CODE_MAP`).
    api_key : str, optional
        Defaults to settings.fred_api_key.
    http_get : callable, optional
        Injected for testing. Defaults to httpx.get.
    """
    if series_code not in CODE_MAP:
        raise KeyError(f"unknown FRED series code: {series_code}")
    fred_id = CODE_MAP[series_code]
    settings = get_settings()
    key = api_key or settings.fred_api_key.get_secret_value()
    if not key:
        log.warning("fred.no_api_key")
        return []

    params = {
        "series_id": fred_id,
        "observation_start": start.isoformat(),
        "observation_end": end.isoformat(),
        "api_key": key,
        "file_type": "json",
    }
    url = f"{FRED_BASE}/series/observations"
    getter = http_get or (lambda u, p: httpx.get(u, params=p, timeout=30))
    try:
        resp = getter(url, params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("fred.fetch_failed", series=series_code, error=str(e))
        return []

    out: list[MacroPoint] = []
    now = datetime.now(UTC)
    for obs in data.get("observations", []):
        raw_v = obs.get("value")
        if raw_v in (None, "", "."):  # FRED uses "." for missing
            continue
        try:
            v = float(raw_v)
            d = datetime.strptime(obs["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        out.append(MacroPoint(
            series_code=series_code, ts=d, value=v, source="fred", as_of_ts=now,
        ))
    return out
