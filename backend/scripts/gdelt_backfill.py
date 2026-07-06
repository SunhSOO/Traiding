"""GDELT GKG global news mentions for our ticker universe.

GDELT GKG (Global Knowledge Graph) parses every news article worldwide
and emits structured records with:
- V2Organizations: company names mentioned (with character offsets)
- V2Tone: 7 tone metrics (overall tone, positive score, negative score,
  polarity, activity reference density, self-reference, word count)
- V2Themes: GDELT theme codes (M&A, earnings, layoffs, etc.)

Strategy to stay within 1TB/month free quota:
1. Query last N days only (default 180) → partition pruning saves cost
2. Filter V2Organizations via REGEX_CONTAINS for a batch of our company
   names (one query per batch of 30 names, ~5GB per query)
3. Group by (DATE, normalized_org) and aggregate
4. Map back to our tickers via NameIndex (Apple Inc → AAPL etc.)
5. Insert into news_articles + news_ticker_mentions for unified
   downstream consumption

Cost estimate: ~500 companies / 30 per batch = ~17 queries × 5GB = 85GB.
Well within free quota.

Usage:

    uv run python scripts/gdelt_backfill.py [--days 180] [--limit-companies N]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.cloud import bigquery
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import get_settings
from core.db import session_scope
from core.models.news import NewsArticle, NewsTickerMention
from core.models.universe import Security


def _clean_name(name: str) -> str:
    """Strip corp suffixes for matching."""
    s = name.strip()
    for sfx in (" Inc.", " Inc", ", Inc.", ", Inc", " Corporation",
                " Corp.", " Corp", " Co.", " Co", " Ltd.", " Ltd",
                " plc", " PLC", " Company", " Holdings"):
        if s.endswith(sfx):
            s = s[: -len(sfx)].strip(" ,")
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--end", type=str, default=None,
                    help="window end date YYYY-MM-DD (default today) — for historical backfill chunks")
    ap.add_argument("--limit-companies", type=int, default=0,
                    help="cap company name list (0 = all SP500)")
    ap.add_argument("--batch-size", type=int, default=30,
                    help="company names per BigQuery query")
    args = ap.parse_args()

    settings = get_settings()
    project = settings.gcp_project_id if hasattr(settings, "gcp_project_id") else \
        __import__("os").environ.get("GCP_PROJECT_ID", "project-39b6b2ad-4644-4993-aeb")

    end = date.fromisoformat(args.end) if args.end else date.today()
    start = end - timedelta(days=args.days)
    print(f"GDELT GKG backfill :: {start} -> {end} project={project}")

    # Build (cleaned_name, ticker) list
    with session_scope() as s:
        rows = list(s.execute(
            select(Security.name, Security.ticker)
            .where(Security.market == "US", Security.is_active.is_(True))
            .order_by(Security.ticker)
        ).all())
    if args.limit_companies > 0:
        rows = rows[:args.limit_companies]
    # name_clean -> ticker
    name_map: dict[str, str] = {}
    for name, ticker in rows:
        cleaned = _clean_name(name)
        if cleaned and len(cleaned) >= 4:
            name_map[cleaned] = ticker
    print(f"  {len(name_map)} clean company names to search")

    names = list(name_map.keys())
    client = bigquery.Client(project=project)

    total_articles, total_mentions = 0, 0
    all_article_rows: list[dict] = []
    all_mention_rows: list[dict] = []
    seen_dedup: set[str] = set()

    start_yyyymmdd = int(start.strftime("%Y%m%d"))
    end_yyyymmdd = int(end.strftime("%Y%m%d"))
    bytes_scanned_total = 0

    for batch_idx in range(0, len(names), args.batch_size):
        batch = names[batch_idx:batch_idx + args.batch_size]
        # Build REGEX safely
        escaped = [n.replace("'", "\\'") for n in batch]
        regex = "|".join([f"({n})" for n in escaped])
        sql = f"""
        SELECT
          CAST(SUBSTR(CAST(DATE AS STRING), 1, 8) AS DATE FORMAT 'YYYYMMDD') AS ts_date,
          V2Organizations,
          V2Tone,
          DocumentIdentifier,
          SourceCommonName
        FROM `gdelt-bq.gdeltv2.gkg_partitioned`
        WHERE _PARTITIONTIME BETWEEN TIMESTAMP('{start.isoformat()}') AND TIMESTAMP('{end.isoformat()}')
          AND V2Organizations IS NOT NULL
          AND REGEXP_CONTAINS(V2Organizations, r'(?i){regex}')
        LIMIT 100000
        """
        try:
            job = client.query(sql)
            df = job.to_dataframe()
            bytes_scanned_total += job.total_bytes_processed or 0
        except Exception as e:
            print(f"  batch {batch_idx//args.batch_size}: FAIL {type(e).__name__}: {e}")
            continue

        # Per row, match each batch name into V2Organizations
        for _, row in df.iterrows():
            v2_orgs = (row.get("V2Organizations") or "").lower()
            v2_tone = (row.get("V2Tone") or "").split(",")
            try:
                tone_overall = float(v2_tone[0]) if v2_tone and v2_tone[0] else 0.0
            except ValueError:
                tone_overall = 0.0
            doc_id = row.get("DocumentIdentifier") or ""
            src = row.get("SourceCommonName") or ""
            ts_date = row.get("ts_date")
            if ts_date is None:
                continue
            pubdate = datetime.combine(
                ts_date, datetime.min.time(),
            ).replace(tzinfo=UTC)
            # Sentiment: positive if tone > 1, negative if < -1
            sent = "POSITIVE" if tone_overall > 1.0 else ("NEGATIVE" if tone_overall < -1.0 else "NEUTRAL")

            matched_tickers: set[str] = set()
            for name in batch:
                if name.lower() in v2_orgs:
                    matched_tickers.add(name_map[name])
            if not matched_tickers:
                continue

            # Create one synthetic news_article per (doc, day) — dedup by URL+date
            dedup = hashlib.sha1(
                f"gdelt|{doc_id[:128]}|{ts_date}".encode("utf-8")
            ).hexdigest()
            if dedup in seen_dedup:
                continue
            seen_dedup.add(dedup)
            article_id = uuid.uuid4()
            title = f"[GDELT] {src} mention" + (f" ({len(matched_tickers)} cos)" if len(matched_tickers) > 1 else "")
            all_article_rows.append({
                "id": article_id,
                "published_ts": pubdate,
                "title": title[:512],
                "url": (doc_id or f"gdelt://{dedup}")[:1024],
                "publisher": (src or "gdelt")[:64],
                "language": "en",
                "summary": f"tone={tone_overall:.2f} orgs={v2_orgs[:300]}",
                "source": "gdelt",
                "dedup_key": dedup,
                "body_fetched": 0,
                "as_of_ts": datetime.now(UTC),
                "ingested_ts": datetime.now(UTC),
            })
            for tkr in matched_tickers:
                all_mention_rows.append({
                    "article_id": article_id,
                    "article_published_ts": pubdate,
                    "market": "US",
                    "ticker": tkr,
                    "mention_kind": "gdelt",
                    "relevance": 0.65,
                })

        print(f"  batch {batch_idx//args.batch_size + 1}/{(len(names) + args.batch_size - 1)//args.batch_size} "
              f"queried {len(df)} rows, articles={len(all_article_rows)} mentions={len(all_mention_rows)} "
              f"scanned={bytes_scanned_total/1e9:.2f}GB", flush=True)

    print(f"\nInserting into news_articles + news_ticker_mentions...")
    # De-dup within this batch on URL (only first occurrence wins)
    seen_urls: set = set()
    pre_dedup = []
    for r in all_article_rows:
        u = r["url"]
        if u in seen_urls:
            continue
        seen_urls.add(u)
        pre_dedup.append(r)
    all_article_rows = pre_dedup
    # Filter out URLs already in DB (chunked — IN clause 65535 param limit)
    if all_article_rows:
        urls = [r["url"] for r in all_article_rows]
        existing: set = set()
        CHUNK_IN = 5000
        with session_scope() as s:
            for k in range(0, len(urls), CHUNK_IN):
                got = s.scalars(
                    select(NewsArticle.url).where(NewsArticle.url.in_(urls[k:k + CHUNK_IN]))
                ).all()
                existing.update(got)
        if existing:
            all_article_rows = [r for r in all_article_rows if r["url"] not in existing]
        print(f"  after URL dedup: {len(all_article_rows)} articles to insert")

    CHUNK = 500
    if all_article_rows:
        with session_scope() as s:
            for k in range(0, len(all_article_rows), CHUNK):
                stmt = pg_insert(NewsArticle).values(
                    all_article_rows[k:k + CHUNK]
                ).on_conflict_do_nothing(constraint="uq_news_articles_dedup")
                s.execute(stmt)
        total_articles = len(all_article_rows)
    # Re-pull IDs that actually landed (chunked)
    keep_ids: set = set()
    if all_article_rows:
        dedup_keys = [r["dedup_key"] for r in all_article_rows]
        CHUNK_IN = 5000
        with session_scope() as s:
            for k in range(0, len(dedup_keys), CHUNK_IN):
                rows_in_db = list(s.scalars(
                    select(NewsArticle).where(
                        NewsArticle.dedup_key.in_(dedup_keys[k:k + CHUNK_IN])
                    )
                ))
                keep_ids.update(row.id for row in rows_in_db)
    all_mention_rows = [m for m in all_mention_rows if m["article_id"] in keep_ids]
    if all_mention_rows:
        with session_scope() as s:
            for k in range(0, len(all_mention_rows), CHUNK):
                stmt = pg_insert(NewsTickerMention).values(
                    all_mention_rows[k:k + CHUNK]
                ).on_conflict_do_nothing(constraint="pk_news_ticker_mentions")
                s.execute(stmt)
        total_mentions = len(all_mention_rows)

    print(f"\nDone. articles={total_articles} mentions={total_mentions} "
          f"total_bytes_scanned={bytes_scanned_total/1e9:.2f}GB")


if __name__ == "__main__":
    main()
