"""Naver Search API news ingest — KR ticker news depth boost.

Naver gives up to 100 items per query, 1000 queries/day free.
For each KR ticker we issue 1 search per ticker name (display=100).
350 tickers × 1 query = 350 calls, well under quota.

Each result becomes a NewsArticle row + map_article produces
NewsTickerMention rows. Re-running is idempotent (URL conflict).

Usage:

    uv run python scripts/naver_news_backfill.py
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import get_settings
from core.db import session_scope
from core.models.news import NewsArticle, NewsTickerMention
from core.models.universe import Security
from data.news.ticker_mapper import NameIndex, map_article


NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"


def _parse_pubdate(s: str) -> datetime:
    try:
        return parsedate_to_datetime(s)
    except Exception:
        return datetime.now(UTC)


def _strip_html(s: str) -> str:
    out = []
    in_tag = False
    for c in s or "":
        if c == "<":
            in_tag = True
            continue
        if c == ">":
            in_tag = False
            continue
        if not in_tag:
            out.append(c)
    return "".join(out).replace("&quot;", '"').replace("&amp;", "&")


def fetch_news(client_id: str, client_secret: str, query: str, display: int = 100) -> list[dict]:
    r = httpx.get(NAVER_NEWS_URL, params={
        "query": query, "display": display, "sort": "date",
    }, headers={
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }, timeout=20)
    r.raise_for_status()
    return r.json().get("items", []) or []


def build_name_index(session) -> NameIndex:
    kr = [(r.name, r.ticker) for r in session.scalars(
        select(Security).where(Security.market == "KR", Security.is_active.is_(True))
    )]
    us = [(r.name, r.ticker) for r in session.scalars(
        select(Security).where(Security.market == "US", Security.is_active.is_(True))
    )]
    return NameIndex.from_pairs(kr=kr, us=us)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--per-query", type=int, default=100,
                    help="display N per ticker (max 100)")
    args = ap.parse_args()

    settings = get_settings()
    cid = settings.naver_client_id
    csecret = settings.naver_client_secret.get_secret_value()
    if not cid or not csecret:
        print("Naver creds missing")
        sys.exit(1)

    with session_scope() as s:
        tickers = list(s.execute(
            select(Security.ticker, Security.name)
            .where(Security.market == "KR", Security.is_active.is_(True))
            .order_by(Security.ticker)
        ).all())
        index = build_name_index(s)

    print(f"Naver news backfill :: {len(tickers)} KR tickers, "
          f"display={args.per_query}/query")
    if args.limit > 0:
        tickers = tickers[:args.limit]

    now = datetime.now(UTC)
    total_articles, total_mentions, total_fail = 0, 0, 0
    seen_dedup: set[str] = set()
    article_rows: list[dict] = []
    mention_rows: list[dict] = []

    for i, (ticker, name) in enumerate(tickers, start=1):
        try:
            items = fetch_news(cid, csecret, name, display=args.per_query)
        except Exception as e:
            total_fail += 1
            if total_fail <= 5:
                print(f"  fail {ticker} ({name}): {type(e).__name__}: {e}")
            time.sleep(0.6)
            continue

        for it in items:
            url = it.get("link") or ""
            if not url:
                continue
            title = _strip_html(it.get("title", ""))
            summary = _strip_html(it.get("description", ""))
            pubdate = _parse_pubdate(it.get("pubDate", ""))
            publisher = "naver"
            # Dedup key includes title prefix + first 32 chars of url
            dedup = hashlib.sha1(
                f"{title[:80]}|{url[:64]}".encode("utf-8")
            ).hexdigest()
            if dedup in seen_dedup:
                continue
            seen_dedup.add(dedup)
            article_id = uuid.uuid4()
            article_rows.append({
                "id": article_id,
                "published_ts": pubdate,
                "title": title[:1024],
                "url": url[:1024],
                "publisher": publisher,
                "language": "ko",
                "summary": summary[:4096],
                "source": "naver_search",
                "dedup_key": dedup,
                "body_fetched": 0,
                "as_of_ts": now,
                "ingested_ts": now,
            })
            # Map mentions for this article
            for tm in map_article(
                title=title, summary=summary, source_tags=(), index=index,
            ):
                mention_rows.append({
                    "article_id": article_id,
                    "article_published_ts": pubdate,
                    "market": tm.market.value,
                    "ticker": tm.ticker,
                    "mention_kind": tm.mention_kind,
                    "relevance": tm.relevance,
                })

        time.sleep(0.55)  # 1000/day free; 0.5s+ keeps under 10/sec official limit
        if i % 25 == 0 or i == len(tickers):
            print(f"  [{i:3d}/{len(tickers)}] articles_collected={len(article_rows)} "
                  f"mentions_collected={len(mention_rows)} fail={total_fail}",
                  flush=True)

    print("\nFlushing to DB...")
    # Pre-dedup on URL (in-batch)
    seen_urls: set = set()
    pre = []
    for r in article_rows:
        u = r["url"]
        if u in seen_urls:
            continue
        seen_urls.add(u)
        pre.append(r)
    article_rows = pre
    # Filter existing URLs in DB (chunked)
    if article_rows:
        urls = [r["url"] for r in article_rows]
        existing: set = set()
        CHUNK_IN = 5000
        with session_scope() as s:
            for k in range(0, len(urls), CHUNK_IN):
                got = s.scalars(
                    select(NewsArticle.url).where(NewsArticle.url.in_(urls[k:k + CHUNK_IN]))
                ).all()
                existing.update(got)
        if existing:
            article_rows = [r for r in article_rows if r["url"] not in existing]
        print(f"  after URL dedup: {len(article_rows)} new articles")

    CHUNK = 500
    if article_rows:
        with session_scope() as s:
            for k in range(0, len(article_rows), CHUNK):
                stmt = pg_insert(NewsArticle).values(article_rows[k:k + CHUNK]).on_conflict_do_nothing(
                    constraint="uq_news_articles_dedup"
                )
                s.execute(stmt)
        total_articles = len(article_rows)
    # Re-pull article IDs that actually landed (chunked to dodge 65535 cap)
    keep_ids: set = set()
    if article_rows:
        dedup_keys = [r["dedup_key"] for r in article_rows]
        CHUNK_IN = 5000
        with session_scope() as s:
            for k in range(0, len(dedup_keys), CHUNK_IN):
                rows_in_db = list(s.scalars(
                    select(NewsArticle).where(
                        NewsArticle.dedup_key.in_(dedup_keys[k:k + CHUNK_IN])
                    )
                ))
                keep_ids.update(row.id for row in rows_in_db)
    mention_rows = [m for m in mention_rows if m["article_id"] in keep_ids]
    if mention_rows:
        with session_scope() as s:
            for k in range(0, len(mention_rows), CHUNK):
                stmt = pg_insert(NewsTickerMention).values(mention_rows[k:k + CHUNK]).on_conflict_do_nothing(
                    constraint="pk_news_ticker_mentions"
                )
                s.execute(stmt)
        total_mentions = len(mention_rows)

    print(f"Done. articles_attempted={total_articles} mentions_attempted={total_mentions} fail={total_fail}")


if __name__ == "__main__":
    main()
