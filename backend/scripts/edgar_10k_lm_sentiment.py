"""SEC 10-K text Loughran-McDonald sentiment + readability -- Wave 2.

For each 10-K filing in disclosures (form='10-K'):
1. Download full text from EDGAR (HTML).
2. Extract MD&A and Risk Factors sections.
3. Compute:
   - LM sentiment = (positive_words - negative_words) / total_words
   - Risk factor word count change vs prior year
   - Fog index (readability)
   - "going concern" keyword count
   - Restatement flag (search for "restate"/"restatement")

Persists to new table: disclosure_text_features.

Uses Loughran-McDonald 2018 dictionary (public, free).
URL: https://sraf.nd.edu/loughranmcdonald-master-dictionary/

This script downloads the CSV dictionary on first run.

Usage:
    uv run python scripts/edgar_10k_lm_sentiment.py --max-filings 200
"""
from __future__ import annotations

import argparse
import csv
import io
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
CREATE TABLE IF NOT EXISTS disclosure_text_features (
    market               VARCHAR(8) NOT NULL,
    ticker               VARCHAR(16) NOT NULL,
    period_end           DATE NOT NULL,
    lm_sentiment         DOUBLE PRECISION,
    lm_positive_count    INTEGER,
    lm_negative_count    INTEGER,
    lm_litigious_count   INTEGER,
    lm_uncertainty_count INTEGER,
    risk_factor_word_change_pct DOUBLE PRECISION,
    fog_index            DOUBLE PRECISION,
    going_concern_count  INTEGER,
    restatement_flag     INTEGER,
    word_count           INTEGER,
    as_of_ts             TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (market, ticker, period_end)
);
CREATE INDEX IF NOT EXISTS ix_disclosure_text_ticker
    ON disclosure_text_features (market, ticker, period_end);
"""


LM_DICT_URLS = [
    # Multiple mirror locations (Notre Dame URL changes yearly)
    "https://sraf.nd.edu/wp-content/uploads/2025/09/Loughran-McDonald_MasterDictionary_1993-2024.csv",
    "https://sraf.nd.edu/wp-content/uploads/2024/09/Loughran-McDonald_MasterDictionary_1993-2023.csv",
    "https://sraf.nd.edu/wp-content/uploads/2023/11/Loughran-McDonald_MasterDictionary_1993-2021.csv",
    "https://drive.google.com/uc?export=download&id=17CmUZM9hGUdGFOmqM6Ph7eIi3J3jiPph",
    "https://raw.githubusercontent.com/lschmelzeisen/loughran-mcdonald-data/master/data/Loughran-McDonald_MasterDictionary_2018.csv",
]


def load_lm_dictionary(cache_dir: Path) -> dict[str, set[str]]:
    """Load Loughran-McDonald dictionary from pysentiment2 package
    (bundled CSV, 86,486 words, all 4 sentiment categories)."""
    import os
    import pysentiment2
    import pandas as pd
    csv_path = os.path.join(os.path.dirname(pysentiment2.__file__),
                              "static", "LM.csv")
    df = pd.read_csv(csv_path)

    def _set(col):
        return set(df.loc[df[col] > 0, "Word"].str.lower().tolist())

    dictionary = {
        "positive": _set("Positive"),
        "negative": _set("Negative"),
        "litigious": _set("Litigious"),
        "uncertainty": _set("Uncertainty"),
    }
    print(f"L-M dict loaded: +{len(dictionary['positive'])} -{len(dictionary['negative'])} "
          f"L{len(dictionary['litigious'])} U{len(dictionary['uncertainty'])}")
    return dictionary


def fetch_filing_text(url: str, ua: str) -> str:
    try:
        r = httpx.get(url, headers={"User-Agent": ua}, timeout=60)
        r.raise_for_status()
    except Exception:
        return ""
    raw = r.text
    # Strip HTML tags crudely
    clean = re.sub(r"<[^>]+>", " ", raw)
    clean = re.sub(r"&[a-z]+;", " ", clean)
    clean = re.sub(r"\s+", " ", clean)
    return clean.lower()


def compute_fog(text: str) -> float:
    """Gunning Fog Index = 0.4 * (words/sentences + 100 * complex_words/words)."""
    sentences = max(1, text.count(".") + text.count("!") + text.count("?"))
    words = text.split()
    if not words:
        return float("nan")
    complex_words = sum(1 for w in words if len(w) >= 7 or w.count("a") + w.count("e") + w.count("i") + w.count("o") + w.count("u") >= 3)
    return 0.4 * (len(words) / sentences + 100 * complex_words / len(words))


def compute_sentiment(text: str, lm: dict[str, set[str]]) -> dict:
    words = re.findall(r"[a-z]+", text)
    if not words:
        return {"lm_sentiment": float("nan"), "lm_positive_count": 0,
                "lm_negative_count": 0, "lm_litigious_count": 0,
                "lm_uncertainty_count": 0, "word_count": 0}
    pos = sum(1 for w in words if w in lm["positive"])
    neg = sum(1 for w in words if w in lm["negative"])
    lit = sum(1 for w in words if w in lm["litigious"])
    unc = sum(1 for w in words if w in lm["uncertainty"])
    return {
        "lm_sentiment": (pos - neg) / len(words),
        "lm_positive_count": pos, "lm_negative_count": neg,
        "lm_litigious_count": lit, "lm_uncertainty_count": unc,
        "word_count": len(words),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-filings", type=int, default=200)
    ap.add_argument("--market", default="US", choices=["KR", "US"])
    args = ap.parse_args()

    settings = get_settings()
    ua = getattr(settings, "sec_user_agent", None) or "research-bot research@example.com"

    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print("disclosure_text_features table ensured.")

    cache_dir = Path("var/lm_cache")
    lm = load_lm_dictionary(cache_dir)
    if not lm["positive"]:
        print("L-M dictionary empty -- abort"); sys.exit(1)

    with session_scope() as s:
        rows = list(s.execute(
            select(Disclosure.market, Disclosure.ticker, Disclosure.filing_date,
                    Disclosure.source_url, Disclosure.body_text)
            .where(Disclosure.market == args.market)
            .where(Disclosure.filing_type_canonical == "ANNUAL")
            .order_by(Disclosure.filing_date.desc())
            .limit(args.max_filings)
        ).all())

    print(f"Processing {len(rows)} 10-K filings...")
    total_ok = 0
    for market, ticker, filing_date, url, body_cached in rows:
        # Use cached body_text if available; otherwise fetch from URL
        if body_cached and len(body_cached) > 1000:
            text_body = body_cached.lower()
        elif url:
            time.sleep(0.15)   # SEC rate limit
            text_body = fetch_filing_text(url, ua)
        else:
            continue
        if len(text_body) < 1000:
            continue
        sent = compute_sentiment(text_body, lm)
        fog = compute_fog(text_body)
        going_concern = text_body.count("going concern")
        restate = int("restatement" in text_body or "restated" in text_body)

        with eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO disclosure_text_features
                (market, ticker, period_end, lm_sentiment, lm_positive_count,
                 lm_negative_count, lm_litigious_count, lm_uncertainty_count,
                 risk_factor_word_change_pct, fog_index, going_concern_count,
                 restatement_flag, word_count, as_of_ts)
                VALUES (:market, :ticker, :period_end, :lm_sent, :lm_pos,
                        :lm_neg, :lm_lit, :lm_unc, NULL, :fog, :gc, :rs,
                        :wc, :ats)
                ON CONFLICT (market, ticker, period_end) DO NOTHING
            """), {
                "market": market, "ticker": ticker, "period_end": filing_date,
                "lm_sent": sent["lm_sentiment"], "lm_pos": sent["lm_positive_count"],
                "lm_neg": sent["lm_negative_count"], "lm_lit": sent["lm_litigious_count"],
                "lm_unc": sent["lm_uncertainty_count"], "fog": fog,
                "gc": going_concern, "rs": restate, "wc": sent["word_count"],
                "ats": datetime.now(timezone.utc),
            })
        total_ok += 1
        if total_ok % 20 == 0:
            print(f"  ok={total_ok}/{len(rows)}", flush=True)

    print(f"\nDone. Processed {total_ok}/{len(rows)} 10-K filings.")


if __name__ == "__main__":
    main()
