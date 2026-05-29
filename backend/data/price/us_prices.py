"""US daily-OHLCV adapter — yfinance.

yfinance returns a DataFrame indexed by date with columns
[Open, High, Low, Close, Adj Close, Volume]. We pull per ticker,
but yfinance also supports multi-ticker download which is much
faster — the loader can opt into bulk mode by passing
``fetch_history_bulk`` instead of per-ticker ``fetch_history``.

Lag model: NYSE/NASDAQ close 16:00 ET → as_of 16:02 ET (2 min lag).
yfinance often serves earlier than that but we keep the conservative
close-based bound.
"""
from __future__ import annotations

from datetime import UTC, date as DateType, datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from core.logging import get_logger
from core.types import Market
from data.price.types import DailyBar
from markets.us.calendar import SESSION_CLOSE_LOCAL
from markets.us.ticker import normalize_us_ticker

log = get_logger(__name__)

YFINANCE_DEFAULT_LAG = timedelta(minutes=2)


def fetch_us_daily(
    *,
    ticker: str,
    start: DateType,
    end: DateType,
    fetch_history: Callable[[str, str, str], list[dict]],
    lag: timedelta = YFINANCE_DEFAULT_LAG,
) -> list[DailyBar]:
    """One ticker over [start, end] → DailyBar list.

    Parameters
    ----------
    fetch_history : (ticker, yyyy-mm-dd_start, yyyy-mm-dd_end) -> list[dict]
        Rows are expected with keys date/Date/Datetime,
        open/Open, high/High, low/Low, close/Close,
        adj_close/Adj Close, volume/Volume.
    """
    norm_ticker = normalize_us_ticker(ticker)
    try:
        rows = fetch_history(
            norm_ticker.replace(".", "-"),  # yfinance wants 'BRK-B', we store 'BRK.B'
            start.isoformat(),
            end.isoformat(),
        )
    except Exception as e:
        log.warning("us.price_fetch_failed", ticker=norm_ticker, error=str(e))
        return []

    bars: list[DailyBar] = []
    for row in rows:
        d = _row_date(row)
        if d is None:
            continue
        try:
            o = float(row.get("open") or row.get("Open") or 0)
            h = float(row.get("high") or row.get("High") or 0)
            lo = float(row.get("low") or row.get("Low") or 0)
            c = float(row.get("close") or row.get("Close") or 0)
            v = int(row.get("volume") or row.get("Volume") or 0)
            adj = row.get("adj_close") or row.get("Adj Close") or row.get("AdjClose")
            adj = float(adj) if adj is not None else None
        except (TypeError, ValueError) as e:
            log.warning("us.price_row_unparseable", ticker=norm_ticker, error=str(e))
            continue

        bars.append(DailyBar(
            market=Market.US,
            ticker=norm_ticker,
            trade_date=d,
            open=o, high=h, low=lo, close=c, volume=v,
            adj_close=adj,
            source="yfinance",
            as_of_ts=_as_of_for_us(d, lag),
        ))
    return bars


# ── helpers ──
def _row_date(row: dict) -> Optional[DateType]:
    raw = row.get("trade_date") or row.get("date") or row.get("Date") or row.get("Datetime")
    if raw is None:
        return None
    if isinstance(raw, DateType) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw[:19], fmt).date()
            except ValueError:
                pass
    return None


def _as_of_for_us(d: DateType, lag: timedelta) -> datetime:
    tz = ZoneInfo("America/New_York")
    close_local = datetime.combine(d, SESSION_CLOSE_LOCAL, tzinfo=tz)
    return (close_local + lag).astimezone(UTC)


def build_default_us_price_fetcher() -> dict[str, Callable]:
    import yfinance as yf

    def _history(ticker: str, start: str, end: str) -> list[dict]:
        df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
        if df is None or df.empty:
            return []
        df = df.reset_index()
        return df.to_dict("records")

    return {"fetch_history": _history}
