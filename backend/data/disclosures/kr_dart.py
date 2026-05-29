"""KR disclosure adapter — DART filing list.

Uses OpenDartReader's filing search:
    dart.list(corp, start, end, kind)

Returns DataFrame with columns: rcept_no, corp_code, corp_name,
report_nm, rcept_dt, flr_nm, rm. We normalize to :class:`DisclosureRow`.

`flr_nm` (filing-organization name) and `rm` (remarks) carry useful
context that we drop here — the information module reads the body
when it cares.

`kind` filter (regulatory category) - we pull all "주요" categories
in one call when possible; the loader allows passing kind=None for
"everything in date range".

Canonical type mapping is heuristic from `report_nm`:
- contains '사업보고서' → ANNUAL
- contains '분기보고서' or '반기보고서' → QUARTERLY
- contains '주요사항' → MATERIAL_EVENT
- contains '지분' → INSIDER
- otherwise OTHER
"""
from __future__ import annotations

from datetime import UTC, date as DateType, datetime, timedelta
from typing import Callable, Optional

from core.logging import get_logger
from core.types import Market
from data.disclosures.types import (
    ANNUAL,
    INSIDER,
    MATERIAL_EVENT,
    OTHER,
    QUARTERLY,
    DisclosureRow,
)

log = get_logger(__name__)

DART_FILING_LAG = timedelta(minutes=5)


def fetch_kr_disclosures(
    *,
    corp_code: str,
    ticker: str,
    start: DateType,
    end: DateType,
    fetch_list: Callable[[str, DateType, DateType, Optional[str]], list[dict]],
    kind: Optional[str] = None,
) -> list[DisclosureRow]:
    """Pull DART filings for one corp_code over [start, end].

    Parameters
    ----------
    corp_code : str
        DART 8-digit corp_code.
    ticker : str
        Canonical 6-digit stock ticker (used in our DB).
    fetch_list : (corp_code, start, end, kind) -> list[dict]
        Wraps ``OpenDartReader.list``. Each dict carries:
        rcept_no, corp_code, report_nm, rcept_dt, rcept_no, flr_nm.
    kind : optional
        DART category filter; None for all.
    """
    try:
        rows = fetch_list(corp_code, start, end, kind)
    except Exception as e:
        log.warning("dart.list_failed", corp_code=corp_code, error=str(e))
        return []

    out: list[DisclosureRow] = []
    for row in rows:
        rcept_no = str(row.get("rcept_no") or "").strip()
        if not rcept_no:
            continue
        report_nm = (row.get("report_nm") or row.get("filing_name") or "").strip()
        if not report_nm:
            continue
        rcept_dt = _parse_kr_date(row.get("rcept_dt"))
        if rcept_dt is None:
            continue

        canonical = _canonicalize_kr_type(report_nm)
        filing_ts = datetime.combine(rcept_dt, datetime.min.time(), tzinfo=UTC)
        out.append(DisclosureRow(
            market=Market.KR,
            ticker=ticker,
            source="dart",
            source_id=rcept_no,
            filing_date=rcept_dt,
            filing_type=report_nm,
            filing_type_canonical=canonical,
            title=report_nm,
            source_url=f"http://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
            filing_ts=filing_ts,
            as_of_ts=filing_ts + DART_FILING_LAG,
        ))
    return out


def _parse_kr_date(raw) -> Optional[DateType]:
    if raw is None:
        return None
    if isinstance(raw, DateType) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    try:
        return datetime.strptime(str(raw), "%Y%m%d").date()
    except ValueError:
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d").date()
        except ValueError:
            return None


def _canonicalize_kr_type(report_nm: str) -> str:
    if "사업보고서" in report_nm:
        return ANNUAL
    if "분기보고서" in report_nm or "반기보고서" in report_nm:
        return QUARTERLY
    if "주요사항" in report_nm:
        return MATERIAL_EVENT
    if "지분" in report_nm or "주식등의대량보유" in report_nm or "임원" in report_nm:
        return INSIDER
    return OTHER
