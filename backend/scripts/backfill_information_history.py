"""Historical Information-score backfill driven by FinBERT.

The LLM (Ollama) classification loop only covers ~430 of the 247k
ticker-mentioned articles, so `module_scores` (module='I') has almost no
history — the Information axis was effectively untrained while the
Fundamental and Technical axes had 365-day backfills.

Every news article DOES already carry a FinBERT 3-class sentiment
(`news_finbert_score`, ~1.45M rows). This script bridges those scores
into the existing `score_information` aggregator so the Information axis
gets a real daily time series over the full news window.

Mapping FinBERT -> WeightedMention:
  direction        : finbert_label (positive/negative/neutral) -> +1/-1/0
  impact_magnitude : dominant-class probability bucketed HIGH/MED/LOW
  confidence       : dominant-class probability (0..1)
  source_trust     : information.trust.source_trust(article.source)
  time_weight      : exponential decay, SHORT_TERM horizon (5d half-life)

The aggregation, tanh squash and confidence formula are reused verbatim
from `information.score.score_information`, so FinBERT- and LLM-derived
scores are directly comparable and the LLM loop can overwrite/enrich
later without schema changes.

In-memory design: all mentioned + FinBERT-scored articles (~275k rows)
are loaded once, grouped per (market, ticker), then scored per trading
day via a sliding window. No per-ticker-per-day SQL.

Usage:
    uv run python scripts/backfill_information_history.py            # full news window
    uv run python scripts/backfill_information_history.py --days 120 # trailing N trading days
    uv run python scripts/backfill_information_history.py --lookback 21
"""
from __future__ import annotations

import argparse
import bisect
import math
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg.rows import tuple_row

from core.config import get_settings
from information.score import score_information
from information.trust import WeightedMention, source_trust, time_decay_weight
from information.types import Horizon

HORIZON = Horizon.SHORT_TERM  # FinBERT carries no horizon; news fades fast.


def _pg_dsn() -> str:
    """psycopg3 DSN from the SQLAlchemy URL in settings."""
    url = get_settings().database_url
    # postgresql+psycopg://user:pass@host:port/db -> drop the +driver
    return url.replace("postgresql+psycopg://", "postgresql://")


def _impact_magnitude(prob: float) -> float:
    """Dominant-class probability -> HIGH/MEDIUM/LOW magnitude (1.0/0.5/0.2)."""
    if prob >= 0.65:
        return 1.0
    if prob >= 0.40:
        return 0.5
    return 0.2


def _direction(label: str) -> int:
    l = (label or "").lower()
    if l.startswith("pos"):
        return 1
    if l.startswith("neg"):
        return -1
    return 0


def load_mentions(conn) -> dict[tuple[str, str], list[tuple]]:
    """Return {(market, ticker): [(ts, direction, impact, conf, trust), ...]}
    sorted ascending by ts. One row per (article, ticker) mention that has
    a FinBERT score."""
    sql = """
        SELECT m.market, m.ticker, a.published_ts, a.source,
               f.finbert_positive, f.finbert_negative, f.finbert_neutral,
               f.finbert_label
        FROM news_ticker_mentions m
        JOIN news_articles a
          ON a.id = m.article_id
        JOIN news_finbert_score f
          ON f.article_id = m.article_id
    """
    grouped: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    with conn.cursor(name="mentions_cur", row_factory=tuple_row) as cur:
        cur.itersize = 50_000
        cur.execute(sql)
        for market, ticker, ts, source, pos, neg, neu, label in cur:
            pos, neg, neu = float(pos or 0), float(neg or 0), float(neu or 0)
            direction = _direction(label)
            # Impact reflects how market-moving the news is = strength of the
            # directional signal, NOT classifier certainty. A strongly-neutral
            # article therefore gets LOW impact and cannot inflate confidence.
            impact = _impact_magnitude(max(pos, neg))
            confidence = max(pos, neg, neu)  # classifier certainty
            trust = source_trust(source or "")
            grouped[(market, ticker)].append(
                (ts, direction, impact, confidence, trust)
            )
    for key in grouped:
        grouped[key].sort(key=lambda r: r[0])
    return grouped


def trading_dates(conn, market: str, days: int | None,
                  lo: datetime, hi: datetime) -> list[date]:
    """Distinct trade_dates for the market within [lo, hi] (the news
    window), newest first, optionally capped to `days`."""
    sql = """
        SELECT DISTINCT trade_date FROM daily_prices
        WHERE market = %s AND trade_date >= %s AND trade_date <= %s
        ORDER BY trade_date DESC
    """
    with conn.cursor() as cur:
        cur.execute(sql, (market, lo.date(), hi.date()))
        rows = [r[0] for r in cur.fetchall()]
    if days is not None:
        rows = rows[:days]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None,
                    help="cap to trailing N trading days (default: full news window)")
    ap.add_argument("--lookback", type=int, default=21,
                    help="mention lookback window in days (default 21)")
    args = ap.parse_args()

    dsn = _pg_dsn()
    with psycopg.connect(dsn) as conn:
        print("Loading FinBERT-scored mentions ...", flush=True)
        grouped = load_mentions(conn)
        n_pairs = len(grouped)
        n_rows = sum(len(v) for v in grouped.values())
        print(f"  loaded {n_rows:,} mentions across {n_pairs:,} (market,ticker) pairs",
              flush=True)
        if not grouped:
            print("  no FinBERT-scored mentions — nothing to backfill.")
            return

        # News window bounds — clamp trading-date iteration to it.
        all_ts = [r[0] for rows in grouped.values() for r in rows]
        lo, hi = min(all_ts), max(all_ts)
        print(f"  news window: {lo.date()} .. {hi.date()}", flush=True)

        lookback = timedelta(days=args.lookback)
        upserts: list[tuple] = []

        for market in ("KR", "US"):
            dates = trading_dates(conn, market, args.days, lo, hi)
            tickers = [t for (mk, t) in grouped if mk == market]
            print(f"[{market}] {len(dates)} trading dates x {len(tickers)} tickers",
                  flush=True)
            written = 0
            for d in dates:
                as_of = datetime.combine(d, time(23, 0), tzinfo=timezone.utc)
                win_start = as_of - lookback
                for ticker in tickers:
                    rows = grouped[(market, ticker)]
                    ts_list = [r[0] for r in rows]
                    # window = (win_start, as_of]
                    i0 = bisect.bisect_right(ts_list, win_start)
                    i1 = bisect.bisect_right(ts_list, as_of)
                    if i1 <= i0:
                        continue
                    mentions = []
                    for ts, direction, impact, conf, trust in rows[i0:i1]:
                        age = (as_of - ts).total_seconds() / 86400.0
                        mentions.append(WeightedMention(
                            ticker=ticker, market=market,
                            direction=direction, impact_magnitude=impact,
                            confidence=conf, source_trust=trust,
                            time_weight=time_decay_weight(age, HORIZON),
                        ))
                    info = score_information(mentions)
                    upserts.append((
                        as_of, market, ticker, "I",
                        info.score, info.confidence, "finbert-backfill",
                        psycopg.types.json.Json({
                            "n_mentions": info.n_mentions,
                            "breakdown": info.breakdown,
                            "src": "finbert",
                        }),
                    ))
                    written += 1
            print(f"  [{market}] scored {written:,} ticker-days", flush=True)

        # Bulk upsert.
        print(f"Upserting {len(upserts):,} module_scores rows ...", flush=True)
        UP = """
            INSERT INTO module_scores
                (computed_ts, market, ticker, module, score, confidence,
                 model_version, inputs)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT ON CONSTRAINT pk_module_scores DO UPDATE SET
                score = EXCLUDED.score,
                confidence = EXCLUDED.confidence,
                model_version = EXCLUDED.model_version,
                inputs = EXCLUDED.inputs
        """
        CH = 5000
        with conn.cursor() as cur:
            for i in range(0, len(upserts), CH):
                cur.executemany(UP, upserts[i:i + CH])
                conn.commit()
                print(f"  upserted {min(i+CH, len(upserts)):,}/{len(upserts):,}",
                      flush=True)
        print("DONE.", flush=True)


if __name__ == "__main__":
    main()
