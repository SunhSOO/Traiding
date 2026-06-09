"""FinBERT scoring of existing news_articles + earnings transcripts — Wave 3.

For every news article (title + summary), compute FinBERT 3-class sentiment.

Writes to new table: news_finbert_score.

Aggregator features then become available via features_information_v2:
  - finbert_pos_count_7d, finbert_neg_count_7d, finbert_neutral_count_7d
  - finbert_net_sentiment_30d
  - finbert_sentiment_volatility_30d

For earnings: separately parses SEC 8-K Item 2.02 exhibits (earnings press
releases) and scores body text with FinBERT.

Usage:
    uv run python scripts/finbert_news_score.py --batch 500 --max 5000
    uv run python scripts/finbert_news_score.py --source earnings --max 100
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from core.db import get_engine, session_scope
from core.models.news import NewsArticle, NewsTickerMention


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS news_finbert_score (
    article_id            UUID NOT NULL,
    article_published_ts  TIMESTAMP WITH TIME ZONE NOT NULL,
    finbert_positive      DOUBLE PRECISION,
    finbert_negative      DOUBLE PRECISION,
    finbert_neutral       DOUBLE PRECISION,
    finbert_label         VARCHAR(16),
    as_of_ts              TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (article_id)
);
CREATE INDEX IF NOT EXISTS ix_finbert_published
    ON news_finbert_score (article_published_ts);
"""


def score_news(max_articles: int, batch_size: int) -> None:
    from training.models_v3 import FinBERTScorer
    scorer = FinBERTScorer()
    if not scorer.available():
        print("FinBERT not available (transformers missing)"); return

    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print("news_finbert_score table ensured.")

    # Resume support: skip already scored
    with session_scope() as s:
        scored_ids = set(s.scalars(text(
            "SELECT article_id FROM news_finbert_score"
        )).all())

    BATCH = 5000
    offset = 0
    scored_total = 0
    while scored_total < max_articles:
        with session_scope() as s:
            rows = list(s.execute(
                select(NewsArticle.id, NewsArticle.published_ts,
                       NewsArticle.title, NewsArticle.summary)
                .order_by(NewsArticle.published_ts.desc())
                .offset(offset).limit(BATCH)
            ).all())
        if not rows:
            break
        # Filter out already scored
        rows = [r for r in rows if r[0] not in scored_ids]
        if not rows:
            offset += BATCH; continue

        texts = []
        ids_for_batch = []
        for aid, pts, title, summ in rows[:max_articles - scored_total]:
            t = (title or "") + ". " + (summ or "")
            if len(t.strip()) < 5:
                continue
            texts.append(t[:512])
            ids_for_batch.append((aid, pts))
        if not texts:
            offset += BATCH; continue

        print(f"  scoring {len(texts)} articles (batch {batch_size})…", flush=True)
        scores = scorer.score_batch(texts, batch_size=batch_size)

        insert_rows = []
        for (aid, pts), sc in zip(ids_for_batch, scores):
            label = max(sc, key=sc.get)
            insert_rows.append({
                "article_id": aid, "article_published_ts": pts,
                "finbert_positive": sc["positive"],
                "finbert_negative": sc["negative"],
                "finbert_neutral": sc["neutral"],
                "finbert_label": label,
                "as_of_ts": datetime.now(timezone.utc),
            })
        with eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO news_finbert_score
                (article_id, article_published_ts, finbert_positive,
                 finbert_negative, finbert_neutral, finbert_label, as_of_ts)
                VALUES (:article_id, :article_published_ts, :finbert_positive,
                        :finbert_negative, :finbert_neutral, :finbert_label,
                        :as_of_ts)
                ON CONFLICT (article_id) DO NOTHING
            """), insert_rows)
        scored_total += len(insert_rows)
        print(f"    scored_total={scored_total:,}/{max_articles:,}", flush=True)
        offset += BATCH

    print(f"\nDone. Total scored: {scored_total:,}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="news", choices=["news", "earnings"])
    ap.add_argument("--max", type=int, default=5000)
    ap.add_argument("--batch", type=int, default=16)
    args = ap.parse_args()
    if args.source == "news":
        score_news(args.max, args.batch)
    elif args.source == "earnings":
        print("Earnings call scoring: see scripts/earnings_call_sentiment.py")


if __name__ == "__main__":
    main()
