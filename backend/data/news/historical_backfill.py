"""Historical news backfill — multi-source, resumable, rate-limited.

The existing per-source adapters (``bigkinds``, ``gdelt``, ``rss``,
``naver_search``) handle one query at a time. This module orchestrates
*deep historical* backfills: walk every ticker in the universe, fetch
news month-by-month from every enabled source, persist progress so a
restart resumes mid-run.

Design notes:

- **Chunking by month** — months are coarse enough that one chunk's
  failure doesn't lose much progress, fine enough that a single ticker
  query can come back complete (BIGKinds returns at most ~1000 articles
  per query; per month is rarely near the cap).
- **Resumability** — ``BackfillProgress`` rows are the cursor. The
  planner only emits chunks whose status is not ``done``. On crash,
  the next run skips done chunks automatically.
- **Per-source rate limits** — free APIs have aggressive throttles
  (GDELT: 10/min, BIGKinds: 100/min, Naver: 25k/day). We sleep
  ``min_interval_seconds`` between adapter calls for each source.
- **Pure planner + IO-bound runner** — ``plan_chunks`` is a pure
  function; ``run_chunk`` does the IO. Tests cover the planner;
  the runner is integration-tested via mocked adapters.

What this does NOT do:
- It does NOT change the existing ``sync_news`` loader — historical
  backfill is a separate pipeline that feeds the same DB tables.
- It does NOT classify articles (Phase 2's LLM classifier picks them
  up later from ``NewsArticle``).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Callable, Iterable, Optional

from core.logging import get_logger
from data.news.types import NewsArticleRow

log = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TickerSpec:
    market: str
    ticker: str
    query: str           # the query string to send adapters (usually the company name)
    languages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BackfillChunk:
    """One unit of work: (source, ticker, period)."""
    job_id: str
    source: str
    market: str
    ticker: str
    period: str          # YYYY-MM
    query: str
    start: datetime
    end: datetime


@dataclass
class BackfillProgressRow:
    """Detached snapshot of a BackfillProgress DB row.
    Used by the pure planner so it doesn't need a session."""
    source: str
    market: str
    ticker: str
    period: str
    status: str
    rows_inserted: int = 0


@dataclass
class ChunkResult:
    chunk: BackfillChunk
    rows_inserted: int
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ──────────────────────────────────────────────────────────────────────
# Planner — pure
# ──────────────────────────────────────────────────────────────────────


def plan_chunks(
    *,
    job_id: str,
    tickers: Iterable[TickerSpec],
    sources: Iterable[str],
    start: date,
    end: date,
    existing: Iterable[BackfillProgressRow] = (),
) -> list[BackfillChunk]:
    """Build the list of (source × ticker × month) chunks the runner
    still needs to do.

    ``existing`` is the set of progress rows already persisted for
    this ``job_id``. Chunks with ``status == "done"`` are excluded.
    Chunks with ``status == "error"`` are RE-emitted (so the next run
    retries them); ``in_progress`` are also re-emitted on the
    assumption that a previous attempt crashed.
    """
    if end < start:
        raise ValueError("end before start")

    done_keys: set[tuple[str, str, str, str]] = {
        (e.source, e.market, e.ticker, e.period)
        for e in existing if e.status == "done"
    }

    periods = list(_months_in_range(start, end))
    out: list[BackfillChunk] = []
    for t in tickers:
        for s in sources:
            for p, p_start, p_end in periods:
                key = (s, t.market, t.ticker, p)
                if key in done_keys:
                    continue
                out.append(BackfillChunk(
                    job_id=job_id, source=s,
                    market=t.market, ticker=t.ticker, period=p,
                    query=t.query,
                    start=datetime.combine(p_start, datetime.min.time(), tzinfo=UTC),
                    end=datetime.combine(p_end, datetime.max.time(), tzinfo=UTC),
                ))
    return out


def _months_in_range(start: date, end: date) -> Iterable[tuple[str, date, date]]:
    """Yield (YYYY-MM, month_start, month_end_inclusive) covering
    [start, end] (clipped to whole months in between)."""
    cur = date(start.year, start.month, 1)
    while cur <= end:
        # End of this month
        if cur.month == 12:
            next_month = date(cur.year + 1, 1, 1)
        else:
            next_month = date(cur.year, cur.month + 1, 1)
        month_end = next_month - timedelta(days=1)
        # Clip to range; skip inverted chunks so an end-before-start
        # window (or any degenerate month) yields nothing.
        chunk_start = max(cur, start)
        chunk_end = min(month_end, end)
        if chunk_start <= chunk_end:
            yield (cur.strftime("%Y-%m"), chunk_start, chunk_end)
        cur = next_month


# ──────────────────────────────────────────────────────────────────────
# Rate limiter — pure
# ──────────────────────────────────────────────────────────────────────


@dataclass
class RateLimiter:
    """Enforce a minimum interval between consecutive calls per source.

    ``last_call_ts`` is a dict the caller persists across runs (or in
    memory for tests). The limiter computes ``wait_seconds`` and the
    caller decides whether to actually sleep — keeps the limiter
    independent of the clock.
    """
    min_interval_seconds: dict[str, float]
    last_call_ts: dict[str, float] = field(default_factory=dict)

    def wait_seconds_for(self, source: str, now: float) -> float:
        last = self.last_call_ts.get(source)
        interval = self.min_interval_seconds.get(source, 0.0)
        if last is None or interval <= 0:
            return 0.0
        elapsed = now - last
        return max(0.0, interval - elapsed)

    def mark(self, source: str, now: float) -> None:
        self.last_call_ts[source] = now


# Defaults tuned to each source's documented free-tier rate limit,
# with a 2x safety margin.
DEFAULT_RATE_LIMITS: dict[str, float] = {
    "bigkinds":     1.5,   # 100/min documented → 1 call / 0.6s, we use 1.5s
    "gdelt":        7.0,   # ~10/min → 6s, we use 7s
    "naver_search": 0.5,   # 25k/day generous; 2/s is plenty
    "rss":          1.0,
}


# ──────────────────────────────────────────────────────────────────────
# Runner — IO-bound, but adapters are injected for testability
# ──────────────────────────────────────────────────────────────────────


AdapterFn = Callable[[BackfillChunk], list[NewsArticleRow]]
Persister = Callable[[BackfillChunk, list[NewsArticleRow]], int]
StatusUpdater = Callable[[BackfillChunk, str, int, Optional[str]], None]
Sleeper = Callable[[float], None]


def run_chunks(
    chunks: list[BackfillChunk],
    *,
    adapters: dict[str, AdapterFn],
    persist: Persister,
    update_status: StatusUpdater,
    limiter: RateLimiter,
    sleeper: Sleeper = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    max_chunks: Optional[int] = None,
) -> list[ChunkResult]:
    """Walk ``chunks`` in order, calling the right adapter, persisting,
    and stamping progress.

    Parameters
    ----------
    adapters : dict
        Map ``source_name → fn(chunk) -> list[NewsArticleRow]``.
        Missing sources are silently skipped (with a warning log).
    persist :
        Caller-provided. Returns the number of articles inserted.
        The runner does NOT know the DB; the persister wraps the
        existing news loader path.
    update_status :
        Updates ``BackfillProgress`` to ``in_progress`` before each
        chunk and ``done``/``error`` afterwards.
    max_chunks :
        Soft cap on chunks per invocation — useful when a cron job
        wants to chip away at a long backfill in bounded batches.
    """
    results: list[ChunkResult] = []
    for i, chunk in enumerate(chunks):
        if max_chunks is not None and i >= max_chunks:
            break

        adapter = adapters.get(chunk.source)
        if adapter is None:
            log.warning("backfill.unknown_source", source=chunk.source)
            update_status(chunk, "error", 0, f"unknown source: {chunk.source}")
            results.append(ChunkResult(chunk=chunk, rows_inserted=0,
                                       error=f"unknown source: {chunk.source}"))
            continue

        # Rate-limit
        wait = limiter.wait_seconds_for(chunk.source, now_fn())
        if wait > 0:
            sleeper(wait)

        update_status(chunk, "in_progress", 0, None)
        try:
            rows = adapter(chunk)
            limiter.mark(chunk.source, now_fn())
            inserted = persist(chunk, rows)
            update_status(chunk, "done", inserted, None)
            results.append(ChunkResult(chunk=chunk, rows_inserted=inserted))
        except Exception as e:
            log.exception("backfill.chunk_failed",
                          source=chunk.source, ticker=chunk.ticker, period=chunk.period)
            update_status(chunk, "error", 0, str(e))
            results.append(ChunkResult(chunk=chunk, rows_inserted=0, error=str(e)))

    return results
