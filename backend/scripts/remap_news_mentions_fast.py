"""Fast, parallel re-map of every news_article against the NameIndex.

Drop-in replacement for `remap_news_mentions.py`, which was O(n_articles ×
n_tickers) on a *recompiled* regex: `_word_boundary` rebuilt and re.search'd
a fresh pattern for each of ~503 US tickers, per field, per article —
~1.45 billion regex recompilations over 1.44M articles (~18h, never
finished). Two fixes:

1. **One precompiled alternation regex** for all US tickers (503 patterns
   per field collapse to a single `re.findall`). Semantics preserved: the
   per-ticker word-boundary anchors become one group-alternation with a
   trailing look-ahead so adjacent tickers still match.

2. **Multiprocessing** across all CPU cores. Each worker is pure-CPU
   (matching only); the main process owns all DB I/O and streams articles
   in bounded chunks (no full-table ORM load -> no OOM).

Name-substring matching and the _upsert/dedup/top_n ranking reuse the
exact helpers from `data.news.ticker_mapper`, so output is identical to
the slow script — just hours faster.

Idempotent: ON CONFLICT DO NOTHING, existing mentions untouched.

Usage:
    uv run python scripts/remap_news_mentions_fast.py [--workers N] [--chunk 20000]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg.rows import tuple_row

from core.config import get_settings
from core.types import Market
from data.news.ticker_mapper import (
    NameIndex,
    TickerMention,
    _contains_kr,
    _contains_us,
    _upsert,
    _WORD_BOUNDARY_TICKER,
)

# ── worker globals (populated by initializer, once per process) ──
_KR: dict[str, str] = {}
_US: dict[str, str] = {}
_US_TICKER_RE: re.Pattern | None = None
_US_TICKERS: list[str] = []
_TOP_N = 5


def _pg_dsn() -> str:
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")


def _build_ticker_regex(tickers: list[str]) -> re.Pattern:
    """One regex matching ANY US ticker at a word boundary. Replicates
    `_word_boundary`'s anchors `(?:^|[\\s$#(])TICKER(?:[\\s,.;:!?)$]|$)`
    but as a single alternation with a trailing look-ahead (non-consuming,
    so two adjacent tickers both match)."""
    # Longest-first so 'BRKB' is tried before a hypothetical 'BRK'.
    valid = sorted(
        (t for t in tickers if _WORD_BOUNDARY_TICKER.match(t)),
        key=len, reverse=True,
    )
    alt = "|".join(re.escape(t) for t in valid)
    return re.compile(rf"(?:^|[\s$#(])({alt})(?=[\s,.;:!?)$]|$)")


def _init_worker(kr: dict, us: dict, us_tickers: list, top_n: int) -> None:
    global _KR, _US, _US_TICKER_RE, _US_TICKERS, _TOP_N
    _KR, _US, _US_TICKERS, _TOP_N = kr, us, us_tickers, top_n
    _US_TICKER_RE = _build_ticker_regex(us_tickers)


def _map_one(title: str, summary: str) -> list[TickerMention]:
    """Equivalent to map_article(title, summary, source_tags=(), index),
    with the per-ticker word-boundary loop replaced by one regex."""
    title = title or ""
    summary = summary or ""
    seen: dict[tuple[Market, str], TickerMention] = {}

    # 2. Title scan
    for name, ticker in _KR.items():
        if _contains_kr(title, name):
            _upsert(seen, Market.KR, ticker, "title", 0.9)
    for name, ticker in _US.items():
        if _contains_us(title, name):
            _upsert(seen, Market.US, ticker, "title", 0.9)
    for m in _US_TICKER_RE.finditer(title):
        _upsert(seen, Market.US, m.group(1), "title", 0.85)

    # 3. Summary scan (lower weight)
    for name, ticker in _KR.items():
        if _contains_kr(summary, name):
            _upsert(seen, Market.KR, ticker, "body", 0.5)
    for name, ticker in _US.items():
        if _contains_us(summary, name):
            _upsert(seen, Market.US, ticker, "body", 0.5)
    for m in _US_TICKER_RE.finditer(summary):
        _upsert(seen, Market.US, m.group(1), "body", 0.45)

    ranked = sorted(seen.values(), key=lambda x: x.relevance, reverse=True)
    return ranked[:_TOP_N]


def _map_chunk(rows: list[tuple]) -> list[tuple]:
    """rows: [(article_id, published_ts, title, summary), ...]
    -> mention rows ready for insert."""
    out: list[tuple] = []
    for article_id, published_ts, title, summary in rows:
        for mn in _map_one(title, summary):
            out.append((article_id, published_ts, mn.market.value,
                        mn.ticker, mn.mention_kind, mn.relevance))
    return out


def build_index_pairs(conn) -> tuple[dict, dict, list]:
    with conn.cursor() as cur:
        cur.execute("SELECT name, ticker FROM securities "
                    "WHERE market='KR' AND is_active = true")
        kr = cur.fetchall()
        cur.execute("SELECT name, ticker FROM securities "
                    "WHERE market='US' AND is_active = true")
        us = cur.fetchall()
    idx = NameIndex.from_pairs(kr=kr, us=us)
    print(f"  NameIndex: KR={len(idx.kr_name_to_ticker)} names, "
          f"US={len(idx.us_name_to_ticker)} names, "
          f"US tickers={len(idx.us_ticker_set)}", flush=True)
    return (idx.kr_name_to_ticker, idx.us_name_to_ticker,
            sorted(idx.us_ticker_set))


def main() -> None:
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4)))
    ap.add_argument("--chunk", type=int, default=20000,
                    help="articles per worker task / DB stream batch")
    ap.add_argument("--top-n", type=int, default=5)
    args = ap.parse_args()

    dsn = _pg_dsn()
    t0 = time.time()
    # Separate connections: a long-lived read snapshot for the server-side
    # streaming cursor (never committed), and a write connection that
    # commits freely without invalidating the read cursor's portal.
    rconn = psycopg.connect(dsn)
    wconn = psycopg.connect(dsn)
    try:
        kr, us, us_tickers = build_index_pairs(rconn)
        with rconn.cursor() as cur:
            cur.execute("SELECT count(*) FROM news_articles")
            total_articles = cur.fetchone()[0]
        print(f"  articles to scan: {total_articles:,} | workers={args.workers} "
              f"| chunk={args.chunk}", flush=True)

        pool = Pool(args.workers, initializer=_init_worker,
                    initargs=(kr, us, us_tickers, args.top_n))

        # Stream articles via a server-side cursor; dispatch chunks to the
        # pool with imap_unordered so matching overlaps DB I/O.
        scanned = 0
        upserted = 0
        pending: list[tuple] = []

        def flush_inserts(rows: list[tuple]) -> int:
            if not rows:
                return 0
            UP = ("INSERT INTO news_ticker_mentions "
                  "(article_id, article_published_ts, market, ticker, "
                  " mention_kind, relevance) VALUES (%s,%s,%s,%s,%s,%s) "
                  "ON CONFLICT ON CONSTRAINT pk_news_ticker_mentions DO NOTHING")
            with wconn.cursor() as wc:
                wc.executemany(UP, rows)
            wconn.commit()
            return len(rows)

        def chunk_iter():
            nonlocal scanned
            with rconn.cursor(name="art_stream", row_factory=tuple_row) as scur:
                scur.itersize = args.chunk
                scur.execute("SELECT id, published_ts, title, summary "
                             "FROM news_articles ORDER BY published_ts")
                batch: list[tuple] = []
                for row in scur:
                    batch.append(row)
                    if len(batch) >= args.chunk:
                        scanned += len(batch)
                        yield batch
                        batch = []
                if batch:
                    scanned += len(batch)
                    yield batch

        for mention_rows in pool.imap_unordered(_map_chunk, chunk_iter()):
            pending.extend(mention_rows)
            if len(pending) >= 50000:
                upserted += flush_inserts(pending)
                pending = []
            print(f"  scanned ~{scanned:,}/{total_articles:,} "
                  f"| candidate mentions upserted={upserted:,} "
                  f"| {time.time()-t0:.0f}s", flush=True)

        upserted += flush_inserts(pending)
        pool.close()
        pool.join()

        with wconn.cursor() as cur:
            cur.execute("SELECT count(*) FROM news_ticker_mentions")
            total = cur.fetchone()[0]
            cur.execute("SELECT market, count(DISTINCT ticker) "
                        "FROM news_ticker_mentions GROUP BY market")
            by_mkt = cur.fetchall()
    finally:
        rconn.close()
        wconn.close()
    print(f"\nDone in {time.time()-t0:.0f}s. candidate rows upserted={upserted:,} "
          f"(ON CONFLICT DO NOTHING).")
    print(f"  news_ticker_mentions total now: {total:,} | distinct tickers: {by_mkt}")


if __name__ == "__main__":
    main()
