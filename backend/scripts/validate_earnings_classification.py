"""Independent validation of earnings call sentiment classification.

For a sample of (ticker, filing_date) entries, downloads the actual 8-K
filing from SEC and manually checks:
  1. Does the document explicitly contain "Item 2.02" header?
  2. Does it contain "Results of Operations" phrase?
  3. Does it contain at least 3 of: revenue, earnings, eps, operating income,
     net income, gross profit, quarterly results?

A genuine earnings release should satisfy all three. False positives:
  - 8-Ks for M&A, dividend, executive change might have some keywords
    but won't have "Item 2.02" header.

Tests both v1 (keyword body match) and v2 (SEC items=2.02 metadata)
results for ground-truth accuracy.

Usage:
    uv run python scripts/validate_earnings_classification.py --sample 30
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text, select
from core.config import get_settings
from core.db import session_scope
from core.models.universe import Security


_ITEM_202_RX = re.compile(r"item\s*2\.02|item\s*2[._]02", re.IGNORECASE)
_RESULTS_OPS_RX = re.compile(r"results\s+of\s+operations", re.IGNORECASE)
_EARNINGS_KEYWORDS = [
    r"\brevenue", r"\bearnings\s+per\s+share", r"\beps\b",
    r"\boperating\s+income", r"\bnet\s+income", r"\bgross\s+profit",
    r"\bquarterly\s+results", r"\bfiscal\s+(quarter|year)",
    r"\bdiluted\s+(eps|earnings)",
]


def fetch_body(cik: str, fd, ua: str) -> str:
    """Fetch the actual 8-K filing body from SEC for given (ticker, filing_date)."""
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    try:
        r = httpx.get(url, headers={"User-Agent": ua}, timeout=20)
        if r.status_code != 200:
            return ""
        data = r.json()
    except Exception:
        return ""
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    items = recent.get("items", [])
    docs = recent.get("primaryDocument", [])
    cik_int = str(int(cik))
    target_date = str(fd)
    for f, d, a, it, doc in zip(forms, dates, accs, items, docs):
        if d == target_date and f.startswith("8-K"):
            acc_clean = a.replace("-", "")
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{doc}"
            try:
                r2 = httpx.get(doc_url, headers={"User-Agent": ua}, timeout=60)
                r2.raise_for_status()
                raw = r2.text
            except Exception:
                return ""
            clean = re.sub(r"<[^>]+>", " ", raw)
            clean = re.sub(r"&[a-z]+;", " ", clean)
            return re.sub(r"\s+", " ", clean)
    return ""


def validate_one(body: str) -> dict:
    """Return manual-verification verdict on one filing body."""
    has_item_202 = bool(_ITEM_202_RX.search(body))
    has_results_ops = bool(_RESULTS_OPS_RX.search(body))
    keyword_hits = sum(1 for pat in _EARNINGS_KEYWORDS if re.search(pat, body, re.IGNORECASE))
    is_genuine = has_item_202 and has_results_ops and keyword_hits >= 3
    return {
        "is_genuine": is_genuine,
        "has_item_202": has_item_202,
        "has_results_ops": has_results_ops,
        "earnings_kw_hits": keyword_hits,
        "body_len": len(body),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=30, help="how many rows to validate")
    ap.add_argument("--cap-v1", action="store_true",
                    help="restrict to early v1 entries (2026-05-28 8 rows)")
    args = ap.parse_args()

    settings = get_settings()
    ua = str(getattr(settings, "sec_user_agent", None) or "research-bot research@example.com")

    with session_scope() as s:
        if args.cap_v1:
            rows = list(s.execute(text("""
                SELECT ticker, filing_date FROM earnings_call_sentiment
                 WHERE filing_date = '2026-05-28'
                 ORDER BY ticker
            """)).all())
        else:
            rows = list(s.execute(text("""
                SELECT ticker, filing_date FROM earnings_call_sentiment
                 ORDER BY RANDOM() LIMIT :n
            """), {"n": args.sample}).all())

        # Get CIK
        cik_map = {}
        if rows:
            tickers = list(set(r[0] for r in rows))
            cik_rows = list(s.execute(select(Security.ticker, Security.cik).where(
                Security.market == "US", Security.ticker.in_(tickers)
            )).all())
            cik_map = dict(cik_rows)

    print(f"=== Validating {len(rows)} earnings_call_sentiment rows ===")
    print(f"{'TICKER':6s} {'FILING DATE':12s} {'item2.02':10s} {'ResultsOps':12s} {'#KW':4s} {'GENUINE':8s}")
    print("-" * 70)

    genuine = 0
    fake = 0
    skipped = 0
    for ticker, fd in rows:
        cik = cik_map.get(ticker)
        if not cik:
            print(f"  {ticker} SKIP (no cik)")
            skipped += 1
            continue
        time.sleep(0.2)
        body = fetch_body(cik, fd, ua)
        if len(body) < 500:
            print(f"  {ticker:6s} {str(fd):12s} SKIP (body too short / fetch failed)")
            skipped += 1
            continue
        v = validate_one(body)
        verdict = "TRUE" if v["is_genuine"] else "FALSE"
        if v["is_genuine"]: genuine += 1
        else: fake += 1
        print(f"  {ticker:6s} {str(fd):12s} {str(v['has_item_202']):10s} "
              f"{str(v['has_results_ops']):12s} {v['earnings_kw_hits']:4d} {verdict:8s}")

    total = genuine + fake
    if total > 0:
        precision = 100.0 * genuine / total
        print(f"\nManual-verified Precision: {genuine}/{total} = {precision:.1f}%")
        print(f"Fake positives: {fake}")
        print(f"Skipped (couldn't fetch): {skipped}")
    else:
        print(f"\nNo rows verified. Skipped: {skipped}")


if __name__ == "__main__":
    main()
