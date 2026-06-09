"""Extract V2Tone + V2Themes from GDELT-sourced articles' summary field.

When gdelt_backfill.py inserted articles, V2Tone (overall tone score) and
V2Organizations were stored INSIDE the `summary` field as
"tone=X.XX orgs=...". This script parses that summary string into
structured columns and writes a new auxiliary table `gdelt_aux` so the
feature pipeline can aggregate per (ticker, date).

Schema (gdelt_aux):
  article_id, market, ticker, tone, mention_count, themes

Aggregations later by feature pipeline:
  - avg_tone_7d, avg_tone_30d
  - tone_volatility_30d (std of tone)
  - tone_momentum (7d avg - 30d avg)
  - high_negative_tone_count_7d (tone < -3)
  - high_positive_tone_count_7d (tone > +3)

Usage:

    uv run python scripts/gdelt_v2tone_extract.py
"""
from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.dialects.postgresql import UUID, insert as pg_insert
from sqlalchemy.orm import declarative_base, Session

from core.db import session_scope, get_engine
from core.models.news import NewsArticle, NewsTickerMention


# ──────────────────────────────────────────────────────────────────────
# Schema bootstrap — table exists?
# ──────────────────────────────────────────────────────────────────────


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gdelt_aux (
    article_id   UUID NOT NULL,
    article_published_ts TIMESTAMP WITH TIME ZONE NOT NULL,
    market       VARCHAR(8) NOT NULL,
    ticker       VARCHAR(16) NOT NULL,
    tone         DOUBLE PRECISION,
    PRIMARY KEY (article_id, market, ticker)
);
CREATE INDEX IF NOT EXISTS ix_gdelt_aux_market_ticker_ts
    ON gdelt_aux (market, ticker, article_published_ts);
"""


_TONE_RX = re.compile(r"tone=(-?\d+\.\d+)")


def _parse_tone(summary: str | None) -> float | None:
    if not summary:
        return None
    m = _TONE_RX.search(summary)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def main() -> None:
    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print("gdelt_aux table ensured.")

    # Pull GDELT articles in batches + join mentions (gdelt has 1.4M)
    BATCH = 50000
    offset = 0
    total_rows = 0

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
        for article_id, pub_ts, summary, market, ticker in rows:
            tone = _parse_tone(summary)
            if tone is None:
                continue
            insert_rows.append({
                "article_id": article_id, "article_published_ts": pub_ts,
                "market": market, "ticker": ticker, "tone": tone,
            })

        if insert_rows:
            with eng.begin() as conn:
                stmt = text(
                    "INSERT INTO gdelt_aux "
                    "(article_id, article_published_ts, market, ticker, tone) "
                    "VALUES (:article_id, :article_published_ts, :market, :ticker, :tone) "
                    "ON CONFLICT (article_id, market, ticker) DO NOTHING"
                )
                conn.execute(stmt, insert_rows)
            total_rows += len(insert_rows)
        print(f"  offset={offset:,} batch={len(rows):,} kept={len(insert_rows):,} "
              f"total={total_rows:,}", flush=True)

        if len(rows) < BATCH:
            break
        offset += BATCH

    print(f"\nDone. Total tones extracted: {total_rows:,}")


if __name__ == "__main__":
    main()
