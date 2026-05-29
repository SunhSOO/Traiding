"""KR ticker → DART corp_code enrichment.

DART exposes the full corp_code list as a single zipped XML at:

    https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key=...

The archive contains one `corpCode.xml` with rows::

    <list>
      <corp_code>00126380</corp_code>     ← 8-digit DART id
      <corp_name>삼성전자</corp_name>
      <stock_code>005930</stock_code>     ← KRX 6-digit ticker (BLANK for non-listed)
      <modify_date>20240101</modify_date>
    </list>

We download once per refresh, parse, and produce {ticker: corp_code}.
Tickers without a stock_code (private companies) are ignored.

Without the DART_API_KEY, the function logs and returns an empty map
so the caller continues without enrichment.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from typing import Callable, Optional

import httpx

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

DART_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"


def fetch_dart_corp_codes(
    *,
    api_key: Optional[str] = None,
    http_get: Optional[Callable[[str, dict], httpx.Response]] = None,
) -> dict[str, str]:
    """Return ``{ticker: corp_code}`` for every DART-listed KR company."""
    settings = get_settings()
    key = api_key or settings.dart_api_key.get_secret_value()
    if not key:
        log.warning("dart.corpcode.no_api_key")
        return {}

    getter = http_get or (
        lambda u, p: httpx.get(u, params=p, timeout=60, follow_redirects=True)
    )
    try:
        resp = getter(DART_CORPCODE_URL, {"crtfc_key": key})
        resp.raise_for_status()
    except Exception as e:
        log.warning("dart.corpcode.fetch_failed", error=str(e))
        return {}

    return _parse_corpcode_zip(resp.content)


def _parse_corpcode_zip(zip_bytes: bytes) -> dict[str, str]:
    """Extract `CORPCODE.xml` from the zip and parse it."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        log.warning("dart.corpcode.bad_zip", error=str(e))
        return {}

    # Find the XML inside (case-insensitive)
    xml_name = next(
        (n for n in zf.namelist() if n.lower().endswith(".xml")), None,
    )
    if xml_name is None:
        log.warning("dart.corpcode.no_xml_in_zip", names=zf.namelist())
        return {}

    try:
        root = ET.fromstring(zf.read(xml_name))
    except ET.ParseError as e:
        log.warning("dart.corpcode.xml_parse_failed", error=str(e))
        return {}

    out: dict[str, str] = {}
    for node in root.findall("list"):
        stock_code = (node.findtext("stock_code") or "").strip()
        corp_code = (node.findtext("corp_code") or "").strip()
        if not stock_code or len(stock_code) != 6 or not corp_code:
            continue
        out[stock_code] = corp_code
    return out


def apply_corp_codes_to_securities(
    session, corp_code_map: dict[str, str],
) -> int:
    """For every (KR, ticker) in the map, set ``securities.corp_code``.

    Returns count of rows updated. Idempotent: only writes when the
    target row's ``corp_code`` differs from the input.
    """
    if not corp_code_map:
        return 0

    from sqlalchemy import select

    from core.models.universe import Security

    updated = 0
    stmt = select(Security).where(Security.market == "KR")
    for sec in session.scalars(stmt):
        new = corp_code_map.get(sec.ticker)
        if new and sec.corp_code != new:
            sec.corp_code = new
            updated += 1
    session.flush()
    log.info("dart.corpcode.applied", updated=updated, total=len(corp_code_map))
    return updated
