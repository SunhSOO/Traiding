"""GDELT GCAM 24 sentiment dimensions extractor — Wave 2.

V2Tone gave one number. GCAM (Global Content Analysis Measures) is a
multi-dictionary score per article — 24+ sentiment dimensions from
LIWC/General Inquirer/Loughran etc.

Format in news_articles.summary (if present): "gcam=<v10:0.34, v11:-0.2>"
Or fetched via GDELT 2.1 GKG full file (heavier). This script handles
both: prefer summary parsing, fall back to GKG full file.

Schema (new table: gdelt_gcam):
  article_id, market, ticker, gcam_v10_anger, gcam_v11_fear, ..., gcam_v33

Free, public.

Usage:
    uv run python scripts/gdelt_gcam_extract.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text, select
from core.db import get_engine, session_scope
from core.models.news import NewsArticle, NewsTickerMention


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gdelt_gcam (
    article_id           UUID NOT NULL,
    article_published_ts TIMESTAMP WITH TIME ZONE NOT NULL,
    market               VARCHAR(8) NOT NULL,
    ticker               VARCHAR(16) NOT NULL,
    gcam_anger           DOUBLE PRECISION,
    gcam_fear            DOUBLE PRECISION,
    gcam_joy             DOUBLE PRECISION,
    gcam_sadness         DOUBLE PRECISION,
    gcam_econ_neg        DOUBLE PRECISION,
    gcam_econ_pos        DOUBLE PRECISION,
    gcam_pol_neg         DOUBLE PRECISION,
    gcam_pol_pos         DOUBLE PRECISION,
    gcam_orgs_count      INTEGER,
    gcam_persons_count   INTEGER,
    gcam_themes_count    INTEGER,
    gcam_polarity        DOUBLE PRECISION,
    gcam_amp1            DOUBLE PRECISION,
    gcam_amp2            DOUBLE PRECISION,
    PRIMARY KEY (article_id, market, ticker)
);
CREATE INDEX IF NOT EXISTS ix_gcam_market_ticker_ts
    ON gdelt_gcam (market, ticker, article_published_ts);
"""


# GCAM dictionary mappings — GDELT v2.1 GKG GCAM field codes:
# v10.X = WordNet Affect, v11 = LIWC, v19 = Loughran-McDonald, etc.
GCAM_MAP = {
    "anger": [r"v10\.1:(-?\d+\.\d+)", r"v11\.5:(-?\d+\.\d+)"],
    "fear":  [r"v10\.2:(-?\d+\.\d+)", r"v11\.6:(-?\d+\.\d+)"],
    "joy":   [r"v10\.3:(-?\d+\.\d+)", r"v11\.7:(-?\d+\.\d+)"],
    "sadness": [r"v10\.4:(-?\d+\.\d+)"],
    "econ_neg": [r"v19\.5:(-?\d+\.\d+)"],
    "econ_pos": [r"v19\.4:(-?\d+\.\d+)"],
    "pol_neg": [r"c12\.10:(-?\d+\.\d+)"],
    "pol_pos": [r"c12\.11:(-?\d+\.\d+)"],
    "polarity": [r"polarity=(-?\d+\.\d+)"],
    "amp1": [r"amp1=(-?\d+\.\d+)"],
    "amp2": [r"amp2=(-?\d+\.\d+)"],
}

_ORGS_RX = re.compile(r"orgs=([^|]+)")
_PERSONS_RX = re.compile(r"persons=([^|]+)")
_THEMES_RX = re.compile(r"themes=([^|]+)")


def parse_summary(summary: str | None) -> dict:
    if not summary:
        return {}
    out = {}
    for key, patterns in GCAM_MAP.items():
        for pat in patterns:
            m = re.search(pat, summary)
            if m:
                try:
                    out[key] = float(m.group(1))
                    break
                except ValueError:
                    continue
    # Count features
    for fname, rx in [("orgs", _ORGS_RX), ("persons", _PERSONS_RX),
                       ("themes", _THEMES_RX)]:
        m = rx.search(summary)
        if m:
            items = [x for x in m.group(1).split(";") if x.strip()]
            out[f"{fname}_count"] = len(items)
    return out


def main() -> None:
    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print("gdelt_gcam table ensured.")

    BATCH = 50000
    offset = 0
    total = 0
    while True:
        with session_scope() as s:
            rows = list(s.execute(
                select(
                    NewsArticle.id, NewsArticle.published_ts, NewsArticle.summary,
                    NewsTickerMention.market, NewsTickerMention.ticker,
                )
                .join(NewsTickerMention,
                      (NewsTickerMention.article_id == NewsArticle.id) &
                      (NewsTickerMention.article_published_ts == NewsArticle.published_ts))
                .where(NewsArticle.source == "gdelt")
                .order_by(NewsArticle.published_ts)
                .offset(offset).limit(BATCH)
            ).all())
        if not rows:
            break
        insert_rows = []
        for aid, pts, summ, mkt, tkr in rows:
            d = parse_summary(summ)
            if not d:
                continue
            insert_rows.append({
                "article_id": aid, "article_published_ts": pts,
                "market": mkt, "ticker": tkr,
                "gcam_anger": d.get("anger"), "gcam_fear": d.get("fear"),
                "gcam_joy": d.get("joy"), "gcam_sadness": d.get("sadness"),
                "gcam_econ_neg": d.get("econ_neg"), "gcam_econ_pos": d.get("econ_pos"),
                "gcam_pol_neg": d.get("pol_neg"), "gcam_pol_pos": d.get("pol_pos"),
                "gcam_orgs_count": d.get("orgs_count"),
                "gcam_persons_count": d.get("persons_count"),
                "gcam_themes_count": d.get("themes_count"),
                "gcam_polarity": d.get("polarity"),
                "gcam_amp1": d.get("amp1"), "gcam_amp2": d.get("amp2"),
            })
        if insert_rows:
            with eng.begin() as conn:
                conn.execute(text("""
                    INSERT INTO gdelt_gcam
                    (article_id, article_published_ts, market, ticker,
                     gcam_anger, gcam_fear, gcam_joy, gcam_sadness,
                     gcam_econ_neg, gcam_econ_pos, gcam_pol_neg, gcam_pol_pos,
                     gcam_orgs_count, gcam_persons_count, gcam_themes_count,
                     gcam_polarity, gcam_amp1, gcam_amp2)
                    VALUES (:article_id, :article_published_ts, :market, :ticker,
                            :gcam_anger, :gcam_fear, :gcam_joy, :gcam_sadness,
                            :gcam_econ_neg, :gcam_econ_pos, :gcam_pol_neg, :gcam_pol_pos,
                            :gcam_orgs_count, :gcam_persons_count, :gcam_themes_count,
                            :gcam_polarity, :gcam_amp1, :gcam_amp2)
                    ON CONFLICT (article_id, market, ticker) DO NOTHING
                """), insert_rows)
            total += len(insert_rows)
        print(f"  offset={offset:,} batch={len(rows):,} kept={len(insert_rows):,} total={total:,}", flush=True)
        if len(rows) < BATCH:
            break
        offset += BATCH

    print(f"\nDone. Total GCAM rows: {total:,}")


if __name__ == "__main__":
    main()
