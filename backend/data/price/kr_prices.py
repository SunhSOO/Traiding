"""KR daily-OHLCV adapter — pykrx.

pykrx exposes per-ticker DataFrames (``stock.get_market_ohlcv``) and
investor-net flows (``stock.get_market_trading_value_by_date``). We
fetch both per ticker and zip them into :class:`DailyBar` rows.

Lag model: a KR bar for date D becomes "known" at session_close + 2min
(KRX close 15:30 KST → as_of 15:32 KST). Even though pykrx might serve
the data later, we use the close time as the lower bound so the as_of
filter doesn't push backtests "into the future" relative to live.
"""
from __future__ import annotations

from datetime import UTC, date as DateType, datetime, time, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from core.logging import get_logger
from core.types import Market
from data.price.types import DailyBar
from markets.kr.calendar import SESSION_CLOSE_LOCAL
from markets.kr.ticker import normalize_kr_ticker

log = get_logger(__name__)

PYKRX_DEFAULT_LAG = timedelta(minutes=2)


def fetch_kr_daily(
    *,
    ticker: str,
    start: DateType,
    end: DateType,
    fetch_ohlcv: Callable[[str, str, str], list[dict]],
    fetch_investor: Optional[Callable[[str, str, str], list[dict]]] = None,
    lag: timedelta = PYKRX_DEFAULT_LAG,
    source: str = "pykrx",
) -> list[DailyBar]:
    """Return DailyBar list for one KR ticker over [start, end].

    Parameters
    ----------
    ticker : str
        Will be normalized; safe to pass raw.
    fetch_ohlcv : (yyyymmdd_from, yyyymmdd_to, ticker) -> list[dict]
        Wraps ``pykrx.stock.get_market_ohlcv``. Each dict expected
        to have keys: date | trade_date, open|Open, high|High,
        low|Low, close|Close, volume|Volume.
    fetch_investor : optional same shape, returning rows with
        foreign_net / institution_net columns.
    """
    norm_ticker = normalize_kr_ticker(ticker)
    fr = start.strftime("%Y%m%d")
    to = end.strftime("%Y%m%d")

    try:
        ohlcv_rows = fetch_ohlcv(fr, to, norm_ticker)
    except Exception as e:
        log.warning("kr.price_fetch_failed", ticker=norm_ticker, error=str(e))
        return []

    investor_rows: list[dict] = []
    if fetch_investor is not None:
        try:
            investor_rows = fetch_investor(fr, to, norm_ticker)
        except Exception as e:
            log.warning("kr.investor_fetch_failed", ticker=norm_ticker, error=str(e))

    investor_by_date: dict[DateType, dict] = {}
    for row in investor_rows:
        d = _row_date(row)
        if d is not None:
            investor_by_date[d] = row

    bars: list[DailyBar] = []
    for row in ohlcv_rows:
        d = _row_date(row)
        if d is None:
            continue
        try:
            o = float(row.get("open") or row.get("Open") or row.get("시가") or 0)
            h = float(row.get("high") or row.get("High") or row.get("고가") or 0)
            lo = float(row.get("low") or row.get("Low") or row.get("저가") or 0)
            c = float(row.get("close") or row.get("Close") or row.get("종가") or 0)
            v = int(row.get("volume") or row.get("Volume") or row.get("거래량") or 0)
        except (TypeError, ValueError) as e:
            log.warning("kr.price_row_unparseable", ticker=norm_ticker, error=str(e))
            continue

        inv = investor_by_date.get(d, {})
        f_net = _to_int(inv.get("foreign") or inv.get("외국인합계"))
        i_net = _to_int(inv.get("institution") or inv.get("기관합계"))

        bars.append(DailyBar(
            market=Market.KR,
            ticker=norm_ticker,
            trade_date=d,
            open=o, high=h, low=lo, close=c, volume=v,
            adj_close=None,
            foreign_net=f_net,
            institution_net=i_net,
            source=source,
            as_of_ts=_as_of_for_kr(d, lag),
        ))
    return bars


# ── helpers ──
def _row_date(row: dict) -> Optional[DateType]:
    # pykrx labels the date index "날짜"; "일자" appears in some KRX exports.
    raw = (row.get("trade_date") or row.get("date") or row.get("Date")
           or row.get("날짜") or row.get("일자"))
    if raw is None:
        return None
    if isinstance(raw, DateType) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                pass
    return None


def _to_int(v) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _as_of_for_kr(d: DateType, lag: timedelta) -> datetime:
    """Convert KR trade date → close-time UTC + lag."""
    tz = ZoneInfo("Asia/Seoul")
    close_local = datetime.combine(d, SESSION_CLOSE_LOCAL, tzinfo=tz)
    return (close_local + lag).astimezone(UTC)


# ── Production wiring ──
def build_default_kr_price_fetcher() -> dict[str, Callable]:
    from pykrx import stock

    def _ohlcv(fr: str, to: str, ticker: str) -> list[dict]:
        df = stock.get_market_ohlcv(fr, to, ticker)
        if df is None or df.empty:
            return []
        df = df.reset_index().rename(columns={"날짜": "date"})
        return df.to_dict("records")

    def _investor(fr: str, to: str, ticker: str) -> list[dict]:
        # pykrx exposes investor breakdowns via separate calls; we
        # bundle foreign + institution into a single per-day dict.
        df = stock.get_market_net_purchases_of_equities_by_ticker(
            fr, to, "KOSPI", investor="외국인합계"
        )
        # NOTE: real production code should call both KOSPI/KOSDAQ and
        # both 외국인/기관 investor codes; this stub is intentionally
        # minimal — full implementation belongs to Phase 1.2 finish
        # after we wire up real pykrx integration tests with a live API.
        return df.reset_index().to_dict("records") if df is not None and not df.empty else []

    return {"fetch_ohlcv": _ohlcv, "fetch_investor": _investor}


def build_yfinance_kr_price_fetcher() -> dict[str, Callable]:
    """KR OHLCV via yfinance (.KS = KOSPI, .KQ = KOSDAQ).

    Replaces the pykrx feed, which requires a KRX login (KRX_ID/PW) that is
    walled in this environment — pykrx returns empty, freezing KR prices.
    yfinance serves fresh KR daily bars keyed by the 6-digit code + market
    suffix; we try .KS first, then .KQ. Investor-flow breakdowns are not
    available via yfinance → empty (they were login-walled stubs anyway)."""
    import yfinance as yf

    def _ohlcv(fr: str, to: str, ticker: str) -> list[dict]:
        start = f"{fr[:4]}-{fr[4:6]}-{fr[6:8]}"
        # yfinance `end` is exclusive → +1 day to include `to`.
        end_d = DateType(int(to[:4]), int(to[4:6]), int(to[6:8])) + timedelta(days=1)
        end = end_d.isoformat()
        for suffix in (".KS", ".KQ"):
            try:
                h = yf.Ticker(ticker + suffix).history(
                    start=start, end=end, auto_adjust=False,
                )
            except Exception:
                h = None
            if h is not None and not h.empty:
                h = h.reset_index()
                out = []
                for _, r in h.iterrows():
                    dt = r["Date"]
                    out.append({
                        "Date": dt.date() if hasattr(dt, "date") else dt,
                        "Open": r.get("Open"), "High": r.get("High"),
                        "Low": r.get("Low"), "Close": r.get("Close"),
                        "Volume": r.get("Volume"),
                    })
                return out
        return []

    def _investor(fr: str, to: str, ticker: str) -> list[dict]:
        return []   # no KR investor-flow data via yfinance

    return {"fetch_ohlcv": _ohlcv, "fetch_investor": _investor}
