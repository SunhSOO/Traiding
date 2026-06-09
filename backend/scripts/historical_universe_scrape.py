"""Historical S&P 500 / NASDAQ-100 / KOSPI200 constituent history — Wave 3.

Survivorship-bias 보정용. 시점별 listed 종목 추적.

Sources (모두 무료):
  - S&P 500 changes: Wikipedia "List of S&P 500 companies" + 변경 이력 테이블
  - KOSPI 200: 한국거래소 시점별 명단 (PDF/Excel) — 보수적으로 pykrx 사용
  - NASDAQ-100: Wikipedia "NASDAQ-100 historical components"

Schema (new table: universe_history):
  index_name, ticker, market, action ('ADD'|'REMOVE'), effective_date,
  reason, source

Aggregated lookup: 'as_of_date_constituents' view to get listed set on any date.

Usage:
    uv run python scripts/historical_universe_scrape.py
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from core.db import get_engine


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS universe_history (
    index_name      VARCHAR(32) NOT NULL,
    ticker          VARCHAR(16) NOT NULL,
    market          VARCHAR(8) NOT NULL,
    action          VARCHAR(8) NOT NULL,    -- 'ADD' or 'REMOVE'
    effective_date  DATE NOT NULL,
    reason          TEXT,
    source          VARCHAR(64),
    as_of_ts        TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (index_name, ticker, action, effective_date)
);
CREATE INDEX IF NOT EXISTS ix_uhist_date
    ON universe_history (effective_date, index_name);
CREATE INDEX IF NOT EXISTS ix_uhist_ticker
    ON universe_history (market, ticker);
"""


def scrape_sp500_history(ua: str) -> list[dict]:
    """Wikipedia S&P 500 historical changes table."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        r = httpx.get(url, headers={"User-Agent": ua}, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  S&P 500 fetch failed: {e}")
        return []
    import pandas as pd
    import io
    tables = pd.read_html(io.StringIO(r.text))
    rows = []
    now = datetime.now(timezone.utc)
    # Current constituents
    current = tables[0]
    for _, row in current.iterrows():
        sym = str(row.get("Symbol", "") or row.get("Ticker symbol", "")).strip()
        if not sym or sym == "nan":
            continue
        rows.append({
            "index_name": "SP500", "ticker": sym, "market": "US",
            "action": "ADD", "effective_date": date(1990, 1, 1),  # we don't know — historical baseline
            "reason": "current member", "source": "wikipedia/sp500_current",
            "as_of_ts": now,
        })
    # Historical changes (table 1 usually)
    if len(tables) >= 2:
        changes = tables[1]
        for _, row in changes.iterrows():
            try:
                dt_str = str(row.iloc[0])
                # Try parse: "August 23, 2024" or similar
                for fmt in ("%B %d, %Y", "%B %d %Y", "%Y-%m-%d"):
                    try:
                        effective = datetime.strptime(dt_str.strip(), fmt).date()
                        break
                    except ValueError:
                        continue
                else:
                    continue
                added = str(row.iloc[1] if len(row) > 1 else "").strip()
                removed = str(row.iloc[3] if len(row) > 3 else "").strip()
                if added and added != "nan":
                    rows.append({
                        "index_name": "SP500", "ticker": added, "market": "US",
                        "action": "ADD", "effective_date": effective,
                        "reason": str(row.iloc[2] if len(row) > 2 else "")[:300],
                        "source": "wikipedia/sp500_changes", "as_of_ts": now,
                    })
                if removed and removed != "nan":
                    rows.append({
                        "index_name": "SP500", "ticker": removed, "market": "US",
                        "action": "REMOVE", "effective_date": effective,
                        "reason": str(row.iloc[4] if len(row) > 4 else "")[:300],
                        "source": "wikipedia/sp500_changes", "as_of_ts": now,
                    })
            except Exception:
                continue
    return rows


def scrape_nasdaq100_history(ua: str) -> list[dict]:
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    try:
        r = httpx.get(url, headers={"User-Agent": ua}, timeout=30)
        r.raise_for_status()
        import pandas as pd, io
        tables = pd.read_html(io.StringIO(r.text))
    except Exception as e:
        print(f"  NASDAQ-100 fetch failed: {e}")
        return []
    rows = []
    now = datetime.now(timezone.utc)
    for t in tables:
        cols_lower = [str(c).lower() for c in t.columns]
        if any("ticker" in c or "symbol" in c for c in cols_lower):
            for _, row in t.iterrows():
                for col in t.columns:
                    val = str(row[col]).strip()
                    if re.match(r"^[A-Z]{1,5}$", val):
                        rows.append({
                            "index_name": "NASDAQ100", "ticker": val, "market": "US",
                            "action": "ADD", "effective_date": date(1990, 1, 1),
                            "reason": "current member", "source": "wikipedia/nasdaq100",
                            "as_of_ts": now,
                        })
                        break
            break
    return rows


def main() -> None:
    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print("universe_history table ensured.")

    ua = "research-bot research@example.com"
    all_rows = []
    print("Scraping S&P 500...")
    sp_rows = scrape_sp500_history(ua)
    print(f"  S&P 500: {len(sp_rows)} entries")
    all_rows.extend(sp_rows)
    print("Scraping NASDAQ-100...")
    nq_rows = scrape_nasdaq100_history(ua)
    print(f"  NASDAQ-100: {len(nq_rows)} entries")
    all_rows.extend(nq_rows)

    if all_rows:
        with eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO universe_history
                (index_name, ticker, market, action, effective_date,
                 reason, source, as_of_ts)
                VALUES (:index_name, :ticker, :market, :action, :effective_date,
                        :reason, :source, :as_of_ts)
                ON CONFLICT (index_name, ticker, action, effective_date) DO NOTHING
            """), all_rows)
    print(f"\nDone. Total rows inserted/upserted: {len(all_rows):,}")


if __name__ == "__main__":
    main()
