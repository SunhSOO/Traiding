"""Earnings call sentiment via SEC 8-K Item 2.02 exhibits — Wave 3.

SEC 8-K "Item 2.02 Results of Operations and Financial Condition" filings
attach earnings press releases (Exhibit 99.1 usually). Free, public, dated.

This script:
  1. Pulls 8-K filings from disclosures table where filing_type_canonical
     == 'MATERIAL_EVENT' AND title contains 'Item 2.02' or similar
  2. Fetches associated EX-99.x text from EDGAR
  3. Scores with FinBERT + Loughran-McDonald

Writes to: earnings_call_sentiment (market, ticker, period_end,
                                    transcript_sent_finbert, transcript_sent_lm,
                                    word_count)

Usage:
    uv run python scripts/earnings_call_sentiment.py --max 200
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
from core.models.disclosures import Disclosure


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
CREATE INDEX IF NOT EXISTS ix_earn_call_ticker
    ON earnings_call_sentiment (market, ticker, filing_date);
"""


_RESULT_KEYWORDS = re.compile(
    r"results of operations|quarterly results|earnings release|item 2\.02",
    re.IGNORECASE,
)


def fetch_text(url: str, ua: str) -> str:
    try:
        r = httpx.get(url, headers={"User-Agent": ua}, timeout=60)
        r.raise_for_status()
    except Exception:
        return ""
    raw = r.text
    clean = re.sub(r"<[^>]+>", " ", raw)
    clean = re.sub(r"&[a-z]+;", " ", clean)
    return re.sub(r"\s+", " ", clean).lower()


def compute_lm_sentiment(text: str, lm: dict) -> tuple[float, int, int, int]:
    words = re.findall(r"[a-z]+", text)
    if not words:
        return float("nan"), 0, 0, 0
    pos = sum(1 for w in words if w in lm["positive"])
    neg = sum(1 for w in words if w in lm["negative"])
    return (pos - neg) / len(words), pos, neg, len(words)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=200)
    args = ap.parse_args()

    settings = get_settings()
    ua = getattr(settings, "sec_user_agent", None) or "research-bot research@example.com"

    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print("earnings_call_sentiment table ensured.")

    # Load LM dictionary
    import pysentiment2, pandas as pd, os
    csv_path = os.path.join(os.path.dirname(pysentiment2.__file__), "static", "LM.csv")
    lm_df = pd.read_csv(csv_path)
    lm = {
        "positive": set(lm_df.loc[lm_df["Positive"] > 0, "Word"].str.lower().tolist()),
        "negative": set(lm_df.loc[lm_df["Negative"] > 0, "Word"].str.lower().tolist()),
    }
    print(f"LM dict: +{len(lm['positive'])} -{len(lm['negative'])}")

    # FinBERT lazy
    from training.models_v3 import FinBERTScorer
    finbert = FinBERTScorer()
    has_finbert = finbert.available()
    print(f"FinBERT available: {has_finbert}")

    with session_scope() as s:
        rows = list(s.execute(
            select(Disclosure.market, Disclosure.ticker, Disclosure.filing_date,
                    Disclosure.source_url, Disclosure.body_text)
            .where(Disclosure.filing_type_canonical == "MATERIAL_EVENT")
            .where(Disclosure.market == "US")
            .order_by(Disclosure.filing_date.desc())
            .limit(args.max * 8)   # scan widely; filter by body content
        ).all())
    # Filter to earnings-related by body content
    filtered = []
    for market, ticker, fd, url, body in rows:
        # If body cached, check directly
        if body and _RESULT_KEYWORDS.search(body):
            filtered.append((market, ticker, fd, url, body))
            if len(filtered) >= args.max:
                break
        # If no body, check filing URL itself (defer fetch later)
        elif not body and url:
            filtered.append((market, ticker, fd, url, None))
            if len(filtered) >= args.max:
                break
    rows = filtered
    print(f"Earnings-related 8-K candidates: {len(rows)}")

    total_ok = 0
    for market, ticker, fd, url, body_cached in rows:
        # Use cached body if available, else fetch from URL
        if body_cached and len(body_cached) > 500:
            body = body_cached.lower()
        elif url:
            time.sleep(0.15)
            body = fetch_text(url, ua)
            if not _RESULT_KEYWORDS.search(body):
                continue
        else:
            continue
        if len(body) < 500:
            continue
        # LM
        lm_sent, pos, neg, wc = compute_lm_sentiment(body, lm)
        # FinBERT (truncate to 512 chars for speed; use first paragraph)
        finbert_sent = float("nan")
        if has_finbert:
            try:
                scores = finbert.score_batch([body[:2000]], batch_size=1)
                if scores:
                    finbert_sent = scores[0]["positive"] - scores[0]["negative"]
            except Exception:
                pass

        with eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO earnings_call_sentiment
                (market, ticker, filing_date, transcript_sent_finbert,
                 transcript_sent_lm, transcript_pos_words, transcript_neg_words,
                 word_count, as_of_ts)
                VALUES (:market, :ticker, :fd, :fb, :lm, :pos, :neg, :wc, :ats)
                ON CONFLICT (market, ticker, filing_date) DO NOTHING
            """), {
                "market": market, "ticker": ticker, "fd": fd,
                "fb": finbert_sent, "lm": lm_sent,
                "pos": pos, "neg": neg, "wc": wc,
                "ats": datetime.now(timezone.utc),
            })
        total_ok += 1
        if total_ok % 10 == 0:
            print(f"  ok={total_ok}/{len(rows)}", flush=True)

    print(f"\nDone. {total_ok}/{len(rows)} earnings calls scored.")


if __name__ == "__main__":
    main()
