"""US financials backfill via SEC EDGAR (no API key required, only
SEC_USER_AGENT header set in .env).

Two phases:

1. Enrich securities.cik via SEC company_tickers.json.
2. For every US security with a CIK, pull all historical concept rows
   via the EDGAR companyconcept API.

EDGAR rate limit is 10 req/sec/IP; the adapter sleeps 0.11 s between
calls. Each ticker hits ~N concepts. Expect ~30 minutes for SP500.

Usage:

    uv run python scripts/edgar_backfill_us.py enrich
    uv run python scripts/edgar_backfill_us.py financials [--limit N]
    uv run python scripts/edgar_backfill_us.py all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from core.db import session_scope
from core.models.financials import FinancialFact
from core.models.universe import Security
from core.types import Market
from data.fundamental.loader import sync_financials
from data.fundamental.us_edgar import fetch_us_financials
from data.universe.us_enrichment import (
    apply_ciks_to_securities,
    fetch_sec_ticker_cik_map,
)


def cmd_enrich() -> None:
    print("Fetching SEC ticker -> CIK map ...")
    cik_map = fetch_sec_ticker_cik_map()
    print(f"  SEC map size: {len(cik_map)}")
    if not cik_map:
        print("  FAILED — check SEC_USER_AGENT in .env")
        sys.exit(1)
    with session_scope() as s:
        updated = apply_ciks_to_securities(s, cik_map)
    print(f"  securities.cik updated: {updated}")


def cmd_financials(limit: int, skip_existing_min_concepts: int) -> None:
    with session_scope() as s:
        tickers_with_cik = list(s.execute(
            select(Security.ticker, Security.cik)
            .where(
                Security.market == "US",
                Security.is_active.is_(True),
                Security.cik.isnot(None),
            )
            .order_by(Security.ticker)
        ))
    print(f"  {len(tickers_with_cik)} US tickers have CIK")

    if skip_existing_min_concepts > 0:
        with session_scope() as s:
            already = dict(s.execute(
                select(FinancialFact.ticker, func.count(func.distinct(FinancialFact.concept)))
                .where(FinancialFact.market == "US")
                .group_by(FinancialFact.ticker)
            ).all())
        before = len(tickers_with_cik)
        tickers_with_cik = [
            (t, c) for (t, c) in tickers_with_cik
            if already.get(t, 0) < skip_existing_min_concepts
        ]
        print(f"  skipping {before - len(tickers_with_cik)} tickers already at >= "
              f"{skip_existing_min_concepts} concepts")

    if limit > 0:
        tickers_with_cik = tickers_with_cik[:limit]
        print(f"  limited to first {len(tickers_with_cik)} tickers")

    cik_lookup: dict[str, str] = {t: c for (t, c) in tickers_with_cik}

    def _fetcher(ticker: str):
        cik = cik_lookup.get(ticker)
        if not cik:
            return []
        return fetch_us_financials(cik=cik, ticker=ticker)

    BATCH = 25
    total_processed, total_failed, total_rows = 0, 0, 0
    tickers_only = [t for (t, _) in tickers_with_cik]

    for i in range(0, len(tickers_only), BATCH):
        chunk = tickers_only[i:i + BATCH]
        with session_scope() as s:
            report = sync_financials(
                s, market=Market.US, tickers=chunk, fetcher=_fetcher,
                source_label="edgar",
            )
        total_processed += report.tickers_processed
        total_failed += report.tickers_failed
        total_rows += report.rows_upserted
        print(f"  [{min(i + BATCH, len(tickers_only)):4d}/{len(tickers_only)}] "
              f"processed={total_processed} failed={total_failed} "
              f"rows={total_rows}", flush=True)

    print(f"\nDone. processed={total_processed} failed={total_failed} rows={total_rows}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["enrich", "financials", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-existing-min-concepts", type=int, default=5)
    args = ap.parse_args()

    if args.cmd in ("enrich", "all"):
        cmd_enrich()
    if args.cmd in ("financials", "all"):
        cmd_financials(args.limit, args.skip_existing_min_concepts)


if __name__ == "__main__":
    main()
