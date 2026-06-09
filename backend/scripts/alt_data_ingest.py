"""Alternative data ingest — Wave 2.

Four sources (each independent module function):
1. Wikipedia pageviews (REST API, free)
2. Google Trends (pytrends, free with rate limits)
3. Reddit mentions (PRAW, requires API key but free dev access)
4. USPTO Patents (PatentsView API, free)

Each writes to its own table:
  - wiki_pageviews (ts, market, ticker, views)
  - google_trends (ts, market, ticker, interest)
  - reddit_mentions (ts, market, ticker, mention_count, avg_score)
  - patent_filings (filing_date, market, ticker, count, avg_citations)

Run individually:
    uv run python scripts/alt_data_ingest.py --source wiki --days 365
    uv run python scripts/alt_data_ingest.py --source trends --days 365
    uv run python scripts/alt_data_ingest.py --source reddit --days 90
    uv run python scripts/alt_data_ingest.py --source patents --days 365
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from core.config import get_settings
from core.db import get_engine, session_scope
from core.models.universe import Security


CREATE_TABLES_SQL = {
    "wiki_pageviews": """
        CREATE TABLE IF NOT EXISTS wiki_pageviews (
            ts DATE NOT NULL,
            market VARCHAR(8) NOT NULL,
            ticker VARCHAR(16) NOT NULL,
            views INTEGER NOT NULL,
            page_title VARCHAR(255),
            as_of_ts TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (ts, market, ticker)
        );
        CREATE INDEX IF NOT EXISTS ix_wiki_ticker_ts ON wiki_pageviews (market, ticker, ts);
    """,
    "google_trends": """
        CREATE TABLE IF NOT EXISTS google_trends (
            ts DATE NOT NULL,
            market VARCHAR(8) NOT NULL,
            ticker VARCHAR(16) NOT NULL,
            interest INTEGER NOT NULL,
            keyword VARCHAR(64),
            as_of_ts TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (ts, market, ticker, keyword)
        );
        CREATE INDEX IF NOT EXISTS ix_trends_ticker_ts ON google_trends (market, ticker, ts);
    """,
    "reddit_mentions": """
        CREATE TABLE IF NOT EXISTS reddit_mentions (
            ts DATE NOT NULL,
            market VARCHAR(8) NOT NULL,
            ticker VARCHAR(16) NOT NULL,
            mention_count INTEGER NOT NULL,
            avg_score DOUBLE PRECISION,
            avg_upvote_ratio DOUBLE PRECISION,
            as_of_ts TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (ts, market, ticker)
        );
        CREATE INDEX IF NOT EXISTS ix_reddit_ticker_ts ON reddit_mentions (market, ticker, ts);
    """,
    "patent_filings": """
        CREATE TABLE IF NOT EXISTS patent_filings (
            filing_date DATE NOT NULL,
            market VARCHAR(8) NOT NULL,
            ticker VARCHAR(16) NOT NULL,
            count INTEGER NOT NULL,
            avg_citations DOUBLE PRECISION,
            as_of_ts TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (filing_date, market, ticker)
        );
        CREATE INDEX IF NOT EXISTS ix_patents_ticker_dt ON patent_filings (market, ticker, filing_date);
    """,
}


# ──────────────────────────────────────────────────────────────────────
# 1. Wikipedia Pageviews
# ──────────────────────────────────────────────────────────────────────


def ingest_wiki(days: int, limit: int) -> None:
    eng = get_engine()
    with session_scope() as s:
        rows = list(s.execute(
            select(Security.market, Security.ticker, Security.name)
            .where(Security.is_active == True)
            .limit(limit)
        ).all())

    end = date.today()
    start = end - timedelta(days=days)
    print(f"Wikipedia pageviews :: {len(rows)} tickers, {start}..{end}")
    total = 0
    for market, ticker, name in rows:
        title = name.replace(" ", "_") if name else ticker
        url = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
               f"en.wikipedia/all-access/all-agents/{title}/daily/"
               f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}")
        try:
            r = httpx.get(url, headers={"User-Agent": "research-bot research@example.com"}, timeout=20)
            if r.status_code != 200:
                continue
            items = r.json().get("items", [])
        except Exception:
            continue
        if not items:
            continue
        insert_rows = []
        for it in items:
            ts_str = it["timestamp"][:8]
            try:
                d = datetime.strptime(ts_str, "%Y%m%d").date()
            except ValueError:
                continue
            insert_rows.append({
                "ts": d, "market": market, "ticker": ticker,
                "views": int(it["views"]), "page_title": title[:255],
                "as_of_ts": datetime.now(timezone.utc),
            })
        if insert_rows:
            with eng.begin() as conn:
                conn.execute(text("""
                    INSERT INTO wiki_pageviews
                    (ts, market, ticker, views, page_title, as_of_ts)
                    VALUES (:ts, :market, :ticker, :views, :page_title, :as_of_ts)
                    ON CONFLICT (ts, market, ticker) DO UPDATE SET views = EXCLUDED.views
                """), insert_rows)
            total += len(insert_rows)
        time.sleep(0.1)
    print(f"\nDone. Wiki rows: {total:,}")


# ──────────────────────────────────────────────────────────────────────
# 2. Google Trends (pytrends)
# ──────────────────────────────────────────────────────────────────────


def ingest_trends(days: int, limit: int) -> None:
    try:
        from pytrends.request import TrendReq
    except ImportError:
        print("pytrends not installed: uv add pytrends"); return
    eng = get_engine()
    with session_scope() as s:
        rows = list(s.execute(
            select(Security.market, Security.ticker, Security.name)
            .where(Security.is_active == True).limit(limit)
        ).all())
    end = date.today()
    start = end - timedelta(days=days)
    tf = f"{start.strftime('%Y-%m-%d')} {end.strftime('%Y-%m-%d')}"

    pytrends = TrendReq(hl="en-US", tz=360, retries=3)
    total = 0
    for market, ticker, name in rows:
        kw = f"{ticker} stock"
        try:
            pytrends.build_payload([kw], timeframe=tf)
            df = pytrends.interest_over_time()
        except Exception:
            time.sleep(2); continue
        if df.empty or kw not in df.columns:
            continue
        insert_rows = []
        for ts, row in df.iterrows():
            insert_rows.append({
                "ts": ts.date(), "market": market, "ticker": ticker,
                "interest": int(row[kw]), "keyword": kw,
                "as_of_ts": datetime.now(timezone.utc),
            })
        if insert_rows:
            with eng.begin() as conn:
                conn.execute(text("""
                    INSERT INTO google_trends
                    (ts, market, ticker, interest, keyword, as_of_ts)
                    VALUES (:ts, :market, :ticker, :interest, :keyword, :as_of_ts)
                    ON CONFLICT (ts, market, ticker, keyword) DO UPDATE
                        SET interest = EXCLUDED.interest
                """), insert_rows)
            total += len(insert_rows)
        time.sleep(2)   # pytrends rate limit
    print(f"\nDone. Trends rows: {total:,}")


# ──────────────────────────────────────────────────────────────────────
# 3. Reddit mentions (PRAW)
# ──────────────────────────────────────────────────────────────────────


def ingest_reddit(days: int, limit: int) -> None:
    settings = get_settings()
    cid = getattr(settings, "reddit_client_id", None)
    csec = getattr(settings, "reddit_client_secret", None)
    if not cid or not csec:
        print("Reddit credentials missing in settings; skip"); return
    try:
        import praw
    except ImportError:
        print("praw not installed: uv add praw"); return
    reddit = praw.Reddit(
        client_id=cid.get_secret_value() if hasattr(cid, "get_secret_value") else cid,
        client_secret=csec.get_secret_value() if hasattr(csec, "get_secret_value") else csec,
        user_agent="woonam-auto-trading/0.1",
    )

    eng = get_engine()
    with session_scope() as s:
        rows = list(s.execute(
            select(Security.market, Security.ticker)
            .where(Security.market == "US", Security.is_active == True)
            .limit(limit)
        ).all())
    subs = ["wallstreetbets", "stocks", "investing", "stockmarket"]
    aggregated: dict[tuple, dict] = {}
    for sub in subs:
        try:
            for post in reddit.subreddit(sub).new(limit=500):
                tt = (post.title + " " + (post.selftext or "")).upper()
                created = datetime.fromtimestamp(post.created_utc, tz=timezone.utc).date()
                for market, ticker in rows:
                    if f"${ticker}" in tt or f" {ticker} " in tt:
                        key = (created, market, ticker)
                        e = aggregated.setdefault(key, {"count": 0, "score": 0, "upv": 0})
                        e["count"] += 1
                        e["score"] += post.score
                        e["upv"] += post.upvote_ratio
        except Exception as e:
            print(f"  sub {sub}: FAIL {e}")
    if not aggregated:
        print("No reddit mentions found"); return
    insert_rows = []
    for (ts, market, ticker), v in aggregated.items():
        insert_rows.append({
            "ts": ts, "market": market, "ticker": ticker,
            "mention_count": v["count"],
            "avg_score": v["score"] / v["count"],
            "avg_upvote_ratio": v["upv"] / v["count"],
            "as_of_ts": datetime.now(timezone.utc),
        })
    with eng.begin() as conn:
        conn.execute(text("""
            INSERT INTO reddit_mentions
            (ts, market, ticker, mention_count, avg_score, avg_upvote_ratio, as_of_ts)
            VALUES (:ts, :market, :ticker, :mention_count, :avg_score,
                    :avg_upvote_ratio, :as_of_ts)
            ON CONFLICT (ts, market, ticker) DO UPDATE
                SET mention_count = EXCLUDED.mention_count
        """), insert_rows)
    print(f"\nDone. Reddit rows: {len(insert_rows):,}")


# ──────────────────────────────────────────────────────────────────────
# 4. USPTO PatentsView
# ──────────────────────────────────────────────────────────────────────


def ingest_patents(days: int, limit: int) -> None:
    eng = get_engine()
    with session_scope() as s:
        rows = list(s.execute(
            select(Security.market, Security.ticker, Security.name)
            .where(Security.market == "US", Security.is_active == True).limit(limit)
        ).all())
    end = date.today()
    start = end - timedelta(days=days)
    total = 0
    for market, ticker, name in rows:
        if not name:
            continue
        # PatentsView v1 API
        body = {
            "q": {
                "_and": [
                    {"_contains": {"assignee_organization": name.split()[0]}},
                    {"_gte": {"patent_date": start.isoformat()}},
                    {"_lte": {"patent_date": end.isoformat()}},
                ]
            },
            "f": ["patent_date", "patent_num_cited_by_us_patents"],
            "o": {"page": 1, "per_page": 200},
        }
        try:
            r = httpx.post(
                "https://api.patentsview.org/patents/query",
                json=body, timeout=30,
            )
            if r.status_code != 200:
                continue
            data = r.json().get("patents", [])
        except Exception:
            continue
        if not data:
            continue
        # Aggregate by filing date
        from collections import defaultdict
        agg: dict[date, list] = defaultdict(list)
        for p in data:
            try:
                d = datetime.strptime(p["patent_date"], "%Y-%m-%d").date()
                cites = int(p.get("patent_num_cited_by_us_patents") or 0)
                agg[d].append(cites)
            except (ValueError, KeyError):
                continue
        insert_rows = []
        for d, cites_list in agg.items():
            insert_rows.append({
                "filing_date": d, "market": market, "ticker": ticker,
                "count": len(cites_list),
                "avg_citations": sum(cites_list) / len(cites_list),
                "as_of_ts": datetime.now(timezone.utc),
            })
        if insert_rows:
            with eng.begin() as conn:
                conn.execute(text("""
                    INSERT INTO patent_filings
                    (filing_date, market, ticker, count, avg_citations, as_of_ts)
                    VALUES (:filing_date, :market, :ticker, :count,
                            :avg_citations, :as_of_ts)
                    ON CONFLICT (filing_date, market, ticker) DO UPDATE
                        SET count = EXCLUDED.count
                """), insert_rows)
            total += len(insert_rows)
        time.sleep(0.5)
    print(f"\nDone. Patent rows: {total:,}")


# ──────────────────────────────────────────────────────────────────────
# Main dispatcher
# ──────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    choices=["wiki", "trends", "reddit", "patents", "all"])
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--limit", type=int, default=50,
                    help="Max tickers per source")
    args = ap.parse_args()

    eng = get_engine()
    for name, sql in CREATE_TABLES_SQL.items():
        with eng.begin() as conn:
            for stmt in sql.strip().split(";"):
                if stmt.strip():
                    conn.execute(text(stmt))

    if args.source in ("wiki", "all"):
        ingest_wiki(args.days, args.limit)
    if args.source in ("trends", "all"):
        ingest_trends(args.days, args.limit)
    if args.source in ("reddit", "all"):
        ingest_reddit(args.days, args.limit)
    if args.source in ("patents", "all"):
        ingest_patents(args.days, args.limit)


if __name__ == "__main__":
    main()
