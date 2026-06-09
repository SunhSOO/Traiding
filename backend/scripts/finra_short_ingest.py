"""FINRA Reg SHO daily short volume ingest — Wave 2.

FINRA publishes daily short sale volume per ticker (NYSE + Nasdaq):
https://cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt

Format (pipe-delimited):
  Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market

Bi-monthly Short Interest position from NYSE/NASDAQ for short interest %
of float (use yfinance fallback for daily approx).

Schema (new table: short_volume_daily):
  ts, market, ticker, short_volume, short_exempt, total_volume,
  short_ratio (= short_volume/total_volume)

Usage:
    uv run python scripts/finra_short_ingest.py --days 90
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from core.db import get_engine


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS short_volume_daily (
    ts            DATE NOT NULL,
    market        VARCHAR(8) NOT NULL DEFAULT 'US',
    ticker        VARCHAR(16) NOT NULL,
    short_volume  BIGINT,
    short_exempt  BIGINT,
    total_volume  BIGINT,
    short_ratio   DOUBLE PRECISION,
    as_of_ts      TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (ts, market, ticker)
);
CREATE INDEX IF NOT EXISTS ix_shortvol_ticker
    ON short_volume_daily (market, ticker, ts);
"""


URL_FMT = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt"


def fetch_day(d: date) -> list[dict]:
    url = URL_FMT.format(ymd=d.strftime("%Y%m%d"))
    try:
        r = httpx.get(url, timeout=20)
        if r.status_code != 200:
            return []
    except Exception:
        return []
    text_data = r.text
    rows = []
    for line in text_data.splitlines()[1:]:    # skip header
        parts = line.split("|")
        if len(parts) < 5:
            continue
        try:
            rows.append({
                "ts": d,
                "market": "US",
                "ticker": parts[1].strip(),
                "short_volume": int(parts[2]) if parts[2] else 0,
                "short_exempt": int(parts[3]) if parts[3] else 0,
                "total_volume": int(parts[4]) if parts[4] else 0,
                "short_ratio": (
                    int(parts[2]) / int(parts[4])
                    if parts[2] and parts[4] and int(parts[4]) > 0 else None
                ),
                "as_of_ts": datetime.now(timezone.utc),
            })
        except (ValueError, IndexError):
            continue
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print("short_volume_daily table ensured.")

    end = date.today()
    total = 0
    for off in range(args.days):
        d = end - timedelta(days=off)
        if d.weekday() >= 5:
            continue   # skip weekends
        rows = fetch_day(d)
        if not rows:
            continue
        with eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO short_volume_daily
                (ts, market, ticker, short_volume, short_exempt,
                 total_volume, short_ratio, as_of_ts)
                VALUES (:ts, :market, :ticker, :short_volume, :short_exempt,
                        :total_volume, :short_ratio, :as_of_ts)
                ON CONFLICT (ts, market, ticker) DO NOTHING
            """), rows)
        total += len(rows)
        print(f"  {d}: {len(rows):,} rows (total={total:,})", flush=True)
        time.sleep(0.2)

    print(f"\nDone. Total rows: {total:,}")


if __name__ == "__main__":
    main()
