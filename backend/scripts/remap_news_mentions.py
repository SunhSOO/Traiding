"""Re-map every news_article against the current NameIndex.

Many articles were ingested before the KR universe was loaded, so
their `news_ticker_mentions` rows reflect a US-only index. Re-running
map_article with the full universe produces the KR mentions that were
missed.

Idempotent: new mentions are inserted via ON CONFLICT DO NOTHING.
Existing mentions are not touched.

Usage:

    uv run python scripts/remap_news_mentions.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.db import session_scope
from core.models.news import NewsArticle, NewsTickerMention
from core.models.universe import Security
from data.news.ticker_mapper import NameIndex, map_article


def build_index() -> NameIndex:
    with session_scope() as s:
        kr = [(r.name, r.ticker) for r in s.scalars(
            select(Security).where(Security.market == "KR", Security.is_active.is_(True))
        )]
        us = [(r.name, r.ticker) for r in s.scalars(
            select(Security).where(Security.market == "US", Security.is_active.is_(True))
        )]
    print(f"  NameIndex pairs: KR={len(kr)}, US={len(us)}")
    return NameIndex.from_pairs(kr=kr, us=us)


def main() -> None:
    index = build_index()
    new_rows: list[dict] = []
    seen_keys: set[tuple] = set()
    article_count = 0

    with session_scope() as s:
        for art in s.scalars(select(NewsArticle).order_by(NewsArticle.published_ts)):
            article_count += 1
            mentions = map_article(
                title=art.title, summary=art.summary, source_tags=(), index=index,
            )
            for tm in mentions:
                key = (art.id, tm.market.value, tm.ticker)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                new_rows.append({
                    "article_id": art.id,
                    "article_published_ts": art.published_ts,
                    "market": tm.market.value,
                    "ticker": tm.ticker,
                    "mention_kind": tm.mention_kind,
                    "relevance": tm.relevance,
                })

    print(f"  Articles scanned: {article_count}")
    print(f"  Candidate mention rows: {len(new_rows)}")
    if not new_rows:
        print("  Nothing to upsert.")
        return

    CHUNK = 2000
    inserted_attempted = 0
    with session_scope() as s:
        for i in range(0, len(new_rows), CHUNK):
            chunk = new_rows[i:i + CHUNK]
            stmt = pg_insert(NewsTickerMention).values(chunk).on_conflict_do_nothing(
                constraint="pk_news_ticker_mentions"
            )
            s.execute(stmt)
            inserted_attempted += len(chunk)
    print(f"  Upserted: {inserted_attempted} attempted (ON CONFLICT DO NOTHING)")

    with session_scope() as s:
        total = s.scalar(select(NewsTickerMention).select_from(NewsTickerMention).with_only_columns(
            __import__("sqlalchemy").func.count()
        ))
    print(f"  news_ticker_mentions total now: {total}")


if __name__ == "__main__":
    main()
