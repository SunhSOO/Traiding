"""SEC 8-K + Form 4 disclosures backfill.

Pulls each US security's recent filings via SEC EDGAR submissions API
and stores 8-K (event) and Form 4 (insider transaction) entries in the
disclosures table. Body extraction is deferred — for ML feature
purposes the COUNT and TIMING of insider filings is already a strong
signal.

SEC rate limit: 10 req/sec/IP. Throttled at 0.11s between calls.

Usage:

    uv run python scripts/sec_disclosures_backfill.py [--limit N]
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import get_settings
from core.db import session_scope
from core.models.disclosures import Disclosure
from core.models.universe import Security


SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
THROTTLE = 0.12

# Form types we want — 8-K family + insider Form 4 family
TARGETED_FORMS = {
    "8-K", "8-K/A",
    "4", "4/A",
    "10-K", "10-K/A", "10-Q", "10-Q/A",
    "13F-HR", "13F-HR/A",
    "S-1", "S-3", "S-4",
}


def _canonical_form(form: str) -> str:
    base = form.split("/")[0].strip()
    if base in ("8-K",):
        return "EVENT_8K"
    if base in ("4",):
        return "INSIDER_FORM4"
    if base in ("10-K", "10-Q"):
        return "PERIODIC"
    if base.startswith("13F"):
        return "INSTITUTIONAL"
    if base.startswith("S-"):
        return "REGISTRATION"
    return "OTHER"


def fetch_submissions(cik: str, user_agent: str) -> dict:
    cik_padded = str(cik).zfill(10)
    r = httpx.get(
        SEC_SUBMISSIONS.format(cik=cik_padded),
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--days-back", type=int, default=540,
                    help="ignore filings older than this many days")
    ap.add_argument("--skip-existing-min", type=int, default=5,
                    help="skip ticker that already has this many disclosures")
    args = ap.parse_args()

    settings = get_settings()
    ua = settings.sec_user_agent
    if not ua:
        print("FAIL — SEC_USER_AGENT not set")
        sys.exit(1)

    cutoff = date.today() - timedelta(days=args.days_back)
    print(f"SEC disclosures :: filings >= {cutoff}")

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
    print(f"  {len(tickers_with_cik)} US tickers with CIK")

    if args.skip_existing_min > 0:
        with session_scope() as s:
            already = dict(s.execute(
                select(Disclosure.ticker, func.count())
                .where(Disclosure.market == "US")
                .group_by(Disclosure.ticker)
            ).all())
        before = len(tickers_with_cik)
        tickers_with_cik = [
            (t, c) for (t, c) in tickers_with_cik
            if already.get(t, 0) < args.skip_existing_min
        ]
        print(f"  skipping {before - len(tickers_with_cik)} tickers already at "
              f">= {args.skip_existing_min} disclosures")

    if args.limit > 0:
        tickers_with_cik = tickers_with_cik[:args.limit]
        print(f"  limited to first {len(tickers_with_cik)} tickers")

    now = datetime.now(UTC)
    total_filings = 0
    total_fail = 0
    fail_examples: list[str] = []

    for i, (ticker, cik) in enumerate(tickers_with_cik, start=1):
        try:
            sub = fetch_submissions(cik, ua)
        except Exception as e:
            total_fail += 1
            if len(fail_examples) < 5:
                fail_examples.append(f"{ticker}: {type(e).__name__}")
            time.sleep(THROTTLE)
            continue

        recent = sub.get("filings", {}).get("recent", {})
        accession = recent.get("accessionNumber", [])
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        primary = recent.get("primaryDocument", [])

        rows = []
        for j, form in enumerate(forms):
            if form not in TARGETED_FORMS:
                continue
            fd_str = filing_dates[j] if j < len(filing_dates) else ""
            try:
                fd = datetime.strptime(fd_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if fd < cutoff:
                continue
            acc = accession[j] if j < len(accession) else ""
            prim = primary[j] if j < len(primary) else ""
            url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                   f"{acc.replace('-', '')}/{prim}") if acc and prim else None
            rows.append({
                "id": uuid.uuid4(),
                "market": "US",
                "ticker": ticker,
                "source": "sec",
                "source_id": acc or f"{ticker}-{fd}-{form}",
                "filing_date": fd,
                "filing_ts": None,
                "filing_type": form,
                "filing_type_canonical": _canonical_form(form),
                "title": f"{form} filing",
                "source_url": url,
                "body_text": None,
                "body_fetched": False,
                "amends_id": None,
                "as_of_ts": now,
            })

        if rows:
            CHUNK = 1000
            with session_scope() as s:
                for k in range(0, len(rows), CHUNK):
                    stmt = pg_insert(Disclosure).values(rows[k:k + CHUNK]).on_conflict_do_nothing(
                        constraint="uq_disclosures_source"
                    )
                    s.execute(stmt)
            total_filings += len(rows)

        time.sleep(THROTTLE)

        if i % 50 == 0 or i == len(tickers_with_cik):
            print(f"  [{i:4d}/{len(tickers_with_cik)}] filings_attempted={total_filings} "
                  f"fail={total_fail}", flush=True)

    print(f"\nDone. filings_attempted={total_filings} fail={total_fail}")
    if fail_examples:
        print(f"  first failures: {', '.join(fail_examples)}")


if __name__ == "__main__":
    main()
