"""KR financials adapter — OpenDartReader / DART OpenAPI.

DART exposes financial statements via the ``finstate`` endpoint:
- input: corp_code (8 digits), business_year (YYYY), report_code
  (11013=Q1, 11012=Q2, 11014=Q3, 11011=Annual)
- output: DataFrame rows, each row = one account_id in one statement

We map raw account_id → canonical concept via
:data:`data.fundamental.concepts.DART_MAP`.

DART has a publish lag: quarterly reports are filed within 45 days
of period end, annual within 90. So a 2024-Q1 report's
``as_of_ts`` is roughly ``2024-05-15`` not ``2024-03-31``. We use
the filing date returned by DART when available, else the legal
deadline as conservative fallback.
"""
from __future__ import annotations

from datetime import UTC, date as DateType, datetime, timedelta
from typing import Callable, Optional

from core.logging import get_logger
from core.types import Market
from data.fundamental.concepts import kr_concept_for
from data.fundamental.types import FinancialFactRow

log = get_logger(__name__)

# DART period codes (reprt_code in API)
KR_REPORT_CODES = {
    "Q1": "11013",
    "Q2": "11012",
    "Q3": "11014",
    "ANNUAL": "11011",
}

# Legal filing-deadline lag in days after period end. Used to compute a
# conservative `as_of_ts` when DART doesn't return the filing date.
KR_QUARTERLY_LAG_DAYS = 45
KR_ANNUAL_LAG_DAYS = 90


def fetch_kr_financials(
    *,
    corp_code: str,
    ticker: str,
    year: int,
    period_kind: str,                     # 'Q1' | 'Q2' | 'Q3' | 'ANNUAL'
    fetch_finstate: Callable[[str, int, str, str], list[dict]],
    fs_div: str = "CFS",                  # CFS = consolidated; OFS = separate
) -> list[FinancialFactRow]:
    """Fetch one ticker's financial statements for one period.

    Parameters
    ----------
    corp_code : str
        DART 8-digit corp_code (NOT the same as the 6-digit stock ticker).
    ticker : str
        The 6-digit stock ticker, used as the canonical key.
    fetch_finstate : (corp_code, year, reprt_code, fs_div) -> list[dict]
        Wraps OpenDartReader.finstate-style call. Each dict expected:
        ``{account_id, account_nm, thstrm_amount, currency, fs_nm, ...}``
    fs_div : str
        'CFS' (default, consolidated) or 'OFS' (separate). For most
        liquid stocks we want CFS; some smaller listings only file
        separate.
    """
    if period_kind not in KR_REPORT_CODES:
        raise ValueError(f"unknown KR period_kind: {period_kind}")
    reprt_code = KR_REPORT_CODES[period_kind]

    try:
        rows = fetch_finstate(corp_code, year, reprt_code, fs_div)
    except Exception as e:
        log.warning("dart.fetch_failed", corp_code=corp_code, year=year,
                    period=period_kind, error=str(e))
        return []

    period_end = _period_end_for(year, period_kind)
    period_short = "A" if period_kind == "ANNUAL" else "Q"
    as_of = _as_of_for_kr(period_end, period_kind, rows)

    out: list[FinancialFactRow] = []
    seen_concepts: set[str] = set()    # take first match per concept

    for row in rows:
        raw = row.get("account_id") or row.get("account_nm") or ""
        canonical = kr_concept_for(raw)
        if not canonical or canonical in seen_concepts:
            continue
        raw_value = row.get("thstrm_amount")
        if raw_value in (None, "", "-"):
            continue
        try:
            value = float(str(raw_value).replace(",", ""))
        except (TypeError, ValueError):
            log.warning("dart.unparseable_value", raw=raw_value, account=raw)
            continue

        currency = row.get("currency") or "KRW"
        out.append(FinancialFactRow(
            market=Market.KR, ticker=ticker, concept=canonical,
            period_end=period_end, period_kind=period_short,
            value=value, currency=currency,
            source="dart", raw_concept=raw,
            as_of_ts=as_of,
        ))
        seen_concepts.add(canonical)

    return out


# ── helpers ──
def _period_end_for(year: int, period_kind: str) -> DateType:
    if period_kind == "Q1":
        return DateType(year, 3, 31)
    if period_kind == "Q2":
        return DateType(year, 6, 30)
    if period_kind == "Q3":
        return DateType(year, 9, 30)
    return DateType(year, 12, 31)


def _as_of_for_kr(period_end: DateType, period_kind: str, rows: list[dict]) -> datetime:
    """Pick the filing date from DART if present, else apply legal lag."""
    # DART sometimes includes 'rcept_dt' (접수일자) in YYYYMMDD form.
    for row in rows:
        raw = row.get("rcept_dt")
        if raw:
            try:
                return datetime.strptime(str(raw), "%Y%m%d").replace(tzinfo=UTC)
            except ValueError:
                pass
    lag = KR_ANNUAL_LAG_DAYS if period_kind == "ANNUAL" else KR_QUARTERLY_LAG_DAYS
    return datetime.combine(period_end + timedelta(days=lag), datetime.min.time(), tzinfo=UTC)


# ── Production wiring ──
def build_default_dart_fetcher() -> Callable[[str, int, str, str], list[dict]]:
    """Real OpenDartReader-backed fetcher. Requires DART_API_KEY."""
    from core.config import get_settings

    settings = get_settings()
    key = settings.dart_api_key.get_secret_value()
    if not key:
        raise RuntimeError(
            "DART_API_KEY not configured — set it in .env to enable KR financials"
        )

    import OpenDartReader

    dart = OpenDartReader(key)

    def _fetch(corp_code: str, year: int, reprt_code: str, fs_div: str) -> list[dict]:
        df = dart.finstate_all(corp_code, year, reprt_code=reprt_code, fs_div=fs_div)
        if df is None or df.empty:
            return []
        return df.to_dict("records")

    return _fetch
