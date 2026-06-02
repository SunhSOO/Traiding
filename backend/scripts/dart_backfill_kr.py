"""KR financials backfill via DART OpenAPI.

Two phases:

1. Enrich securities.corp_code via DART corpCode.xml (one ZIP download
   covers all listed companies).
2. For each KR security with a corp_code, pull the last N years of
   quarterly + annual financials, mapping DART account_id → canonical
   concept via data.fundamental.concepts.DART_MAP.

DART rate limit is 1000 requests/key/day (free), reset at midnight KST.
Quarterly statements: 4 quarters × N years × 350 tickers ≈ 5,600 req
for 4 years. Throttled at 1.2s between calls to stay safe.

Usage:

    uv run python scripts/dart_backfill_kr.py enrich
    uv run python scripts/dart_backfill_kr.py financials [--years 4] [--limit N]
    uv run python scripts/dart_backfill_kr.py all
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import func, select

from core.config import get_settings
from core.db import session_scope
from core.models.financials import FinancialFact
from core.models.universe import Security
from core.types import Market
from data.fundamental.kr_dart import fetch_kr_financials, KR_REPORT_CODES
from data.fundamental.loader import sync_financials
from data.universe.kr_enrichment import (
    apply_corp_codes_to_securities, fetch_dart_corp_codes,
)


DART_FINSTATE_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
DART_THROTTLE_S = 1.2  # 1000 req/day free; padding for bursts


def _fetch_finstate(corp_code: str, year: int, reprt_code: str, fs_div: str) -> list[dict]:
    settings = get_settings()
    key = settings.dart_api_key.get_secret_value()
    try:
        r = httpx.get(
            DART_FINSTATE_URL,
            params={
                "crtfc_key": key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            },
            timeout=30,
        )
        r.raise_for_status()
    except Exception:
        return []
    data = r.json()
    if data.get("status") != "000":
        return []
    return data.get("list", []) or []


def cmd_enrich() -> None:
    print("Fetching DART corp_code map (zip download, ~10MB)...")
    corp_map = fetch_dart_corp_codes()
    print(f"  DART corp_code entries: {len(corp_map)}")
    if not corp_map:
        sys.exit(1)
    with session_scope() as s:
        updated = apply_corp_codes_to_securities(s, corp_map)
    print(f"  securities.corp_code updated: {updated}")


_annual_only = False


def cmd_financials(years: int, limit: int, skip_existing_min_concepts: int) -> None:
    with session_scope() as s:
        tickers_with_corp = list(s.execute(
            select(Security.ticker, Security.corp_code)
            .where(
                Security.market == "KR",
                Security.is_active.is_(True),
                Security.corp_code.isnot(None),
            )
            .order_by(Security.ticker)
        ))
    print(f"  {len(tickers_with_corp)} KR tickers have corp_code")

    if skip_existing_min_concepts > 0:
        with session_scope() as s:
            already = dict(s.execute(
                select(FinancialFact.ticker, func.count(func.distinct(FinancialFact.concept)))
                .where(FinancialFact.market == "KR")
                .group_by(FinancialFact.ticker)
            ).all())
        before = len(tickers_with_corp)
        tickers_with_corp = [
            (t, c) for (t, c) in tickers_with_corp
            if already.get(t, 0) < skip_existing_min_concepts
        ]
        print(f"  skipping {before - len(tickers_with_corp)} tickers already at >= "
              f"{skip_existing_min_concepts} concepts")

    if limit > 0:
        tickers_with_corp = tickers_with_corp[:limit]
        print(f"  limited to first {len(tickers_with_corp)} tickers")

    corp_lookup = {t: c for (t, c) in tickers_with_corp}
    current_year = date.today().year
    target_years = list(range(current_year - years, current_year + 1))

    periods = ["ANNUAL"] if _annual_only else list(KR_REPORT_CODES.keys())

    def _fetcher(ticker: str):
        corp = corp_lookup.get(ticker)
        if not corp:
            return []
        out = []
        for yr in target_years:
            for kind in periods:
                rows = fetch_kr_financials(
                    corp_code=corp, ticker=ticker, year=yr,
                    period_kind=kind, fetch_finstate=_fetch_finstate,
                )
                out.extend(rows)
                time.sleep(DART_THROTTLE_S)
        return out

    BATCH = 5
    total_p, total_f, total_r = 0, 0, 0
    tickers_only = [t for (t, _) in tickers_with_corp]
    for i in range(0, len(tickers_only), BATCH):
        chunk = tickers_only[i:i + BATCH]
        with session_scope() as s:
            report = sync_financials(
                s, market=Market.KR, tickers=chunk, fetcher=_fetcher,
                source_label="dart",
            )
        total_p += report.tickers_processed
        total_f += report.tickers_failed
        total_r += report.rows_upserted
        print(f"  [{min(i + BATCH, len(tickers_only)):4d}/{len(tickers_only)}] "
              f"processed={total_p} failed={total_f} rows={total_r}",
              flush=True)
    print(f"\nDone. processed={total_p} failed={total_f} rows={total_r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["enrich", "financials", "all"])
    ap.add_argument("--years", type=int, default=4,
                    help="how many recent years of quarterly+annual to pull")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-existing-min-concepts", type=int, default=5)
    ap.add_argument("--annual-only", action="store_true",
                    help="pull only ANNUAL reports (1/4 of API quota)")
    args = ap.parse_args()
    global _annual_only
    _annual_only = args.annual_only

    if args.cmd in ("enrich", "all"):
        cmd_enrich()
    if args.cmd in ("financials", "all"):
        cmd_financials(args.years, args.limit, args.skip_existing_min_concepts)


if __name__ == "__main__":
    main()
