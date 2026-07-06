"""Resumable, quota-aware 6-year GDELT news backfill driver.

Walks backward in 180-day chunks from the current oldest news date until
either the N-year target is reached OR this month's BigQuery free scan
budget (1 TB) is exhausted. Safe to run repeatedly and monthly: every run
continues from where the last one stopped and self-limits on the *live*
month-to-date bytes billed (queried from INFORMATION_SCHEMA.JOBS), so it
never triggers paid overage on the free tier.

This is the mechanism for the agreed "무료 분할 (spread over months)" 6-year
news backfill: each month's free 1 TB buys ~2 years of history; run monthly
(or via the scheduler) until `oldest <= target_start`.

Usage:
    uv run python scripts/gdelt_history_backfill.py            # run within quota
    uv run python scripts/gdelt_history_backfill.py --dry-run  # plan only, no scan
    uv run python scripts/gdelt_history_backfill.py --target-years 6
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.cloud import bigquery
from sqlalchemy import func, select

from core.db import session_scope
from core.models.news import NewsArticle
from scripts.gdelt_backfill import backfill_window, _resolve_project

# Conservative per-180d-chunk scan estimate, used for the *pre-run* budget
# gate (actual chunks have measured 0.27–0.33 TB). Erring high avoids overage.
EST_CHUNK_TB = 0.35


def _oldest_news_date() -> date | None:
    with session_scope() as s:
        dt = s.execute(select(func.min(NewsArticle.published_ts))).scalar()
    return dt.date() if dt else None


def _month_to_date_tb(client: bigquery.Client) -> float:
    """Live month-to-date bytes *billed* across all jobs in this project."""
    first = date.today().replace(day=1)
    sql = f"""
    SELECT COALESCE(SUM(total_bytes_billed), 0) / 1e12 AS tb
    FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
    WHERE creation_time >= TIMESTAMP('{first.isoformat()}')
    """
    for r in client.query(sql).result():
        return float(r.tb)
    return 0.0


def run_history_backfill(
    *,
    target_years: int = 6,
    free_tb: float = 1.0,
    safety_tb: float = 0.05,
    chunk_days: int = 180,
    dry_run: bool = False,
) -> dict:
    """Walk backward one chunk at a time until the target depth is reached
    or the month's free scan budget is exhausted. Returns a summary dict.
    Callable from the CLI and the monthly scheduler job."""
    client = bigquery.Client(project=_resolve_project())
    target_start = date.today() - timedelta(days=365 * target_years)
    used_tb = _month_to_date_tb(client)
    budget_tb = free_tb - safety_tb
    print(f"[history-backfill] target_start={target_start} "
          f"month_used={used_tb:.3f}TB budget={budget_tb:.3f}TB "
          f"remaining={budget_tb - used_tb:.3f}TB dry={dry_run}", flush=True)

    chunks_run, articles_added = 0, 0
    reason = "done"
    cursor: date | None = None  # dry-run only: simulated oldest (DB doesn't move)
    while True:
        # Real mode re-reads the DB (each chunk moves min date back); dry mode
        # walks a local cursor so the preview doesn't loop on one window.
        oldest = cursor if (dry_run and cursor) else _oldest_news_date()
        if oldest is None:
            print("  no news rows yet - seed with gdelt_backfill first")
            reason = "no_seed"; break
        if oldest <= target_start:
            print(f"  DONE - oldest {oldest} <= target {target_start}")
            reason = "target_reached"; break
        remaining = budget_tb - used_tb
        if remaining < EST_CHUNK_TB:
            print(f"  STOP - month budget exhausted ({remaining:.3f}TB left "
                  f"< ~{EST_CHUNK_TB}TB/chunk). Resume next month; oldest={oldest}",
                  flush=True)
            reason = "quota_exhausted"; break
        end = oldest  # next chunk ends at current oldest (1-day overlap; dedup handles)
        if dry_run:
            print(f"  [dry] would backfill {end - timedelta(days=chunk_days)} "
                  f"-> {end} (~{EST_CHUNK_TB}TB est)", flush=True)
            used_tb += EST_CHUNK_TB
            chunks_run += 1
            cursor = end - timedelta(days=chunk_days)
            continue
        r = backfill_window(end, chunk_days)
        used_tb += r["bytes_scanned"] / 1e12
        chunks_run += 1
        articles_added += r["articles"]
        print(f"  chunk done: {r['start']}->{r['end']} +{r['articles']} articles "
              f"month_used={used_tb:.3f}TB", flush=True)

    oldest_now = _oldest_news_date()
    print(f"[history-backfill] finished: {chunks_run} chunk(s) this run "
          f"(oldest now {oldest_now})", flush=True)
    return {
        "chunks_run": chunks_run,
        "articles_added": articles_added,
        "month_used_tb": used_tb,
        "oldest": oldest_now,
        "reason": reason,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-years", type=int, default=6,
                    help="how far back to backfill (6yr = validated data standard)")
    ap.add_argument("--free-tb", type=float, default=1.0,
                    help="BigQuery free monthly scan tier (TB)")
    ap.add_argument("--safety-tb", type=float, default=0.05,
                    help="headroom left below the free tier")
    ap.add_argument("--chunk-days", type=int, default=180)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan without scanning/inserting")
    args = ap.parse_args()
    run_history_backfill(
        target_years=args.target_years, free_tb=args.free_tb,
        safety_tb=args.safety_tb, chunk_days=args.chunk_days, dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
