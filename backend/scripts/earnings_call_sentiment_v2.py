"""Earnings call sentiment v2 — SEC submissions JSON `items` field — Wave 3.

Differences vs v1 (`earnings_call_sentiment.py`):
  - Pulls each ticker's submissions JSON from data.sec.gov (we have CIK)
  - Filters by `items` array containing "2.02" (Results of Operations)
  - Constructs filing URL directly from accession # + primary document
  - No body_text fallback; direct fetch only

This eliminates the false-positive 84% rate of v1 (keyword scan of body).

Writes to same table: `earnings_call_sentiment` (UPSERT).

Usage:
    uv run python scripts/earnings_call_sentiment_v2.py --max-per-ticker 8 --tickers AAPL,MSFT,NVDA
    uv run python scripts/earnings_call_sentiment_v2.py --limit-tickers 200
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from core.config import get_settings
from core.db import get_engine, session_scope
from core.models.universe import Security


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS earnings_call_sentiment (
    market                   VARCHAR(8) NOT NULL,
    ticker                   VARCHAR(16) NOT NULL,
    filing_date              DATE NOT NULL,
    transcript_sent_finbert  DOUBLE PRECISION,
    transcript_sent_lm       DOUBLE PRECISION,
    transcript_pos_words     INTEGER,
    transcript_neg_words     INTEGER,
    word_count               INTEGER,
    as_of_ts                 TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (market, ticker, filing_date)
);
"""


def fetch_submissions(cik: str, ua: str) -> dict | None:
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    try:
        r = httpx.get(url, headers={"User-Agent": ua,
                                       "Accept": "application/json"}, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def fetch_filing_text(cik: str, accession: str, primary_doc: str, ua: str) -> str:
    """Construct filing URL and download.

    URL: https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_clean}/{primary_doc}
    """
    cik_int = str(int(cik))   # strip leading zeros
    acc_clean = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{primary_doc}"
    try:
        r = httpx.get(url, headers={"User-Agent": ua}, timeout=60)
        r.raise_for_status()
    except Exception:
        return ""
    raw = r.text
    clean = re.sub(r"<[^>]+>", " ", raw)
    clean = re.sub(r"&[a-z]+;", " ", clean)
    return re.sub(r"\s+", " ", clean).lower()


def compute_lm_sentiment(text_body: str, lm: dict) -> tuple[float, int, int, int]:
    words = re.findall(r"[a-z]+", text_body)
    if not words:
        return float("nan"), 0, 0, 0
    pos = sum(1 for w in words if w in lm["positive"])
    neg = sum(1 for w in words if w in lm["negative"])
    return (pos - neg) / len(words), pos, neg, len(words)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-per-ticker", type=int, default=8,
                    help="cap earnings filings per ticker (most recent)")
    ap.add_argument("--limit-tickers", type=int, default=0,
                    help="limit total tickers to process (0 = all)")
    ap.add_argument("--tickers", default="", help="comma-separated subset")
    args = ap.parse_args()

    settings = get_settings()
    ua = getattr(settings, "sec_user_agent", None) or "research-bot research@example.com"
    ua = str(ua)

    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))

    # Load LM dict
    import pysentiment2, pandas as pd, os
    csv_path = os.path.join(os.path.dirname(pysentiment2.__file__), "static", "LM.csv")
    lm_df = pd.read_csv(csv_path)
    lm = {
        "positive": set(lm_df.loc[lm_df["Positive"] > 0, "Word"].str.lower().tolist()),
        "negative": set(lm_df.loc[lm_df["Negative"] > 0, "Word"].str.lower().tolist()),
    }

    # FinBERT
    from training.models_v3 import FinBERTScorer
    finbert = FinBERTScorer()
    has_finbert = finbert.available()
    print(f"FinBERT: {has_finbert} | LM: +{len(lm['positive'])} -{len(lm['negative'])}")

    # Tickers + CIK
    with session_scope() as s:
        q = select(Security.ticker, Security.cik).where(
            Security.market == "US", Security.cik.isnot(None)
        )
        if args.tickers:
            tickers_list = [t.strip().upper() for t in args.tickers.split(",")]
            q = q.where(Security.ticker.in_(tickers_list))
        rows = list(s.execute(q).all())
    if args.limit_tickers > 0:
        rows = rows[:args.limit_tickers]
    print(f"Processing {len(rows)} tickers...")

    # Stats
    n_filings_scanned = 0
    n_filings_2_02 = 0
    n_scored = 0
    n_already_in_db = 0
    n_fetch_fail = 0

    # Check existing
    with session_scope() as s:
        existing = set(s.execute(text(
            "SELECT market, ticker, filing_date FROM earnings_call_sentiment"
        )).all())

    for ticker, cik in rows:
        subs = fetch_submissions(cik, ua)
        time.sleep(0.15)
        if not subs:
            continue
        recent = subs.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accs = recent.get("accessionNumber", [])
        items = recent.get("items", [])
        primary_docs = recent.get("primaryDocument", [])

        # Find 8-K with item 2.02
        matches = []
        for f, d, a, it, doc in zip(forms, dates, accs, items, primary_docs):
            n_filings_scanned += 1
            if f.startswith("8-K") and ("2.02" in (it or "")):
                n_filings_2_02 += 1
                matches.append((d, a, doc))
        matches = matches[:args.max_per_ticker]
        if not matches:
            continue

        for filing_date, acc, doc in matches:
            try:
                fd = datetime.strptime(filing_date, "%Y-%m-%d").date()
            except ValueError:
                continue
            if ("US", ticker, fd) in existing:
                n_already_in_db += 1
                continue
            body = fetch_filing_text(cik, acc, doc, ua)
            time.sleep(0.2)
            if len(body) < 500:
                n_fetch_fail += 1
                continue

            lm_sent, pos, neg, wc = compute_lm_sentiment(body, lm)
            finbert_sent = float("nan")
            if has_finbert:
                try:
                    s_b = finbert.score_batch([body[:2000]], batch_size=1)
                    if s_b:
                        finbert_sent = s_b[0]["positive"] - s_b[0]["negative"]
                except Exception:
                    pass

            with eng.begin() as conn:
                conn.execute(text("""
                    INSERT INTO earnings_call_sentiment
                    (market, ticker, filing_date, transcript_sent_finbert,
                     transcript_sent_lm, transcript_pos_words, transcript_neg_words,
                     word_count, as_of_ts)
                    VALUES ('US', :ticker, :fd, :fb, :lm, :pos, :neg, :wc, :ats)
                    ON CONFLICT (market, ticker, filing_date) DO UPDATE SET
                        transcript_sent_finbert = EXCLUDED.transcript_sent_finbert,
                        transcript_sent_lm = EXCLUDED.transcript_sent_lm,
                        transcript_pos_words = EXCLUDED.transcript_pos_words,
                        transcript_neg_words = EXCLUDED.transcript_neg_words,
                        word_count = EXCLUDED.word_count
                """), {
                    "ticker": ticker, "fd": fd, "fb": finbert_sent, "lm": lm_sent,
                    "pos": pos, "neg": neg, "wc": wc,
                    "ats": datetime.now(timezone.utc),
                })
            n_scored += 1

        if n_scored % 25 == 0 and n_scored > 0:
            print(f"  scanned={n_filings_scanned:,} item-2.02={n_filings_2_02:,} "
                  f"scored={n_scored:,} skip={n_already_in_db:,} fail={n_fetch_fail:,}",
                  flush=True)

    # Final stats
    match_rate = n_scored / max(1, n_filings_2_02 - n_already_in_db) * 100
    print(f"\n=== v2 Summary ===")
    print(f"  Tickers processed       : {len(rows)}")
    print(f"  Total 8-K-ish scanned   : {n_filings_scanned:,}")
    print(f"  Item 2.02 matches       : {n_filings_2_02:,}")
    print(f"  Already in DB (skipped) : {n_already_in_db:,}")
    print(f"  Fetched + scored        : {n_scored:,}")
    print(f"  Fetch failures          : {n_fetch_fail:,}")
    print(f"  Scoring rate (fresh)    : {match_rate:.1f}%")


if __name__ == "__main__":
    main()
