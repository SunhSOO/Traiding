"""Smoke-test every external API key in .env.

Touches each endpoint with a minimal query so failures surface
immediately instead of during a backfill run. No data is stored;
this only verifies authentication + basic connectivity.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from core.config import get_settings


def _result(label: str, ok: bool, detail: str = "") -> None:
    tag = "OK  " if ok else "FAIL"
    print(f"  {tag} {label}" + (f" - {detail}" if detail else ""))


def test_fred() -> None:
    s = get_settings()
    key = s.fred_api_key.get_secret_value() if s.fred_api_key else ""
    if not key:
        _result("FRED", False, "no key in .env")
        return
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id=GS10&api_key={key}&file_type=json&limit=1")
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        n = len(r.json().get("observations", []))
        _result("FRED", n > 0, f"GS10 observations returned ({n})")
    except Exception as e:
        _result("FRED", False, f"{type(e).__name__}: {e}")


def test_naver() -> None:
    s = get_settings()
    cid = s.naver_client_id if s.naver_client_id else ""
    csecret = s.naver_client_secret.get_secret_value() if s.naver_client_secret else ""
    if not cid or not csecret:
        _result("Naver", False, "id/secret missing")
        return
    try:
        r = httpx.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": "삼성전자", "display": 1},
            headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csecret},
            timeout=10,
        )
        r.raise_for_status()
        n = r.json().get("total", 0)
        _result("Naver", n > 0, f"news search total={n}")
    except Exception as e:
        _result("Naver", False, f"{type(e).__name__}: {e}")


def test_dart() -> None:
    s = get_settings()
    key = s.dart_api_key.get_secret_value() if s.dart_api_key else ""
    if not key:
        _result("DART", False, "no key")
        return
    # 삼성전자 corp_code=00126380; list 공시 1건 조회
    try:
        r = httpx.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={"crtfc_key": key, "corp_code": "00126380",
                    "bgn_de": "20240101", "end_de": "20240131",
                    "page_count": 1},
            timeout=15,
        )
        r.raise_for_status()
        d = r.json()
        status = d.get("status", "?")
        ok = status == "000"
        _result("DART", ok, f"status={status} msg={d.get('message', '')}")
    except Exception as e:
        _result("DART", False, f"{type(e).__name__}: {e}")


def test_bok_ecos() -> None:
    s = get_settings()
    key = s.bok_ecos_api_key.get_secret_value() if s.bok_ecos_api_key else ""
    if not key:
        _result("BOK ECOS", False, "no key")
        return
    # 기준금리 (722Y001) — 가장 안전한 호출
    try:
        url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}"
               f"/json/kr/1/5/722Y001/M/202401/202412")
        r = httpx.get(url, timeout=15)
        r.raise_for_status()
        d = r.json()
        if "StatisticSearch" in d:
            n = d["StatisticSearch"]["list_total_count"]
            _result("BOK ECOS", True, f"기준금리 rows={n}")
        else:
            _result("BOK ECOS", False, f"unexpected response: {list(d.keys())}")
    except Exception as e:
        _result("BOK ECOS", False, f"{type(e).__name__}: {e}")


def test_sec_edgar() -> None:
    s = get_settings()
    ua = s.sec_user_agent
    if not ua:
        _result("SEC EDGAR", False, "no SEC_USER_AGENT")
        return
    try:
        r = httpx.get(
            "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues.json",
            headers={"User-Agent": ua}, timeout=15,
        )
        if r.status_code == 404:
            # Apple sometimes files under RevenueFromContractWithCustomerExcludingAssessedTax
            r = httpx.get(
                "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Assets.json",
                headers={"User-Agent": ua}, timeout=15,
            )
        r.raise_for_status()
        units = r.json().get("units", {})
        _result("SEC EDGAR", bool(units), f"AAPL Assets units: {list(units.keys())}")
    except Exception as e:
        _result("SEC EDGAR", False, f"{type(e).__name__}: {e}")


def test_gdelt_bigquery() -> None:
    project = os.environ.get("GCP_PROJECT_ID", "")
    if not project:
        _result("GDELT (BigQuery)", False, "GCP_PROJECT_ID not in .env")
        return
    try:
        from google.cloud import bigquery
    except ImportError:
        _result("GDELT (BigQuery)", False,
                "google-cloud-bigquery not installed (uv add google-cloud-bigquery)")
        return
    try:
        client = bigquery.Client(project=project)
        # Public dataset query — minimal cost
        query = ("SELECT COUNT(*) AS n FROM `gdelt-bq.gdeltv2.events` "
                 "WHERE _PARTITIONTIME = TIMESTAMP('2026-01-01')")
        job = client.query(query)
        rows = list(job.result())
        _result("GDELT (BigQuery)", True, f"GDELT event count for 2026-01-01: {rows[0].n}")
    except Exception as e:
        _result("GDELT (BigQuery)", False, f"{type(e).__name__}: {e}")


def main() -> None:
    print("=" * 60)
    print("API key smoke test")
    print("=" * 60)
    test_fred()
    test_naver()
    test_dart()
    test_bok_ecos()
    test_sec_edgar()
    test_gdelt_bigquery()
    print("=" * 60)


if __name__ == "__main__":
    main()
