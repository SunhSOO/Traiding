"""News ingestion — 5 free sources unified.

| Source        | Best for                                  | Historical depth |
|---------------|-------------------------------------------|------------------|
| BIGKinds      | KR major-press archive, structured tags   | 1990–present     |
| GDELT         | Global news events with tone score        | 1979–present     |
| Naver Search  | KR fresh news, broad publisher coverage   | Limited (~years) |
| RSS           | Real-time per-publisher feeds             | None (live only) |
| Wayback       | Targeted historical URL recovery          | 1996+ snapshots  |

The loader combines all sources, dedups by URL / content hash,
maps each article to tickers via :mod:`data.news.ticker_mapper`,
and upserts into `news_articles` + `news_ticker_mentions`.

LLM-based sentiment / event classification is Phase 2 — it reads
articles from this table.
"""
from __future__ import annotations

from data.news.types import NewsArticleRow

__all__ = ["sync_news", "NewsArticleRow"]


def __getattr__(name):
    # Lazy-load the SQLAlchemy-bound loader so importing sibling
    # pure modules (e.g. historical_backfill, types) doesn't require
    # sqlalchemy at import time.
    if name == "sync_news":
        from data.news.loader import sync_news
        return sync_news
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
