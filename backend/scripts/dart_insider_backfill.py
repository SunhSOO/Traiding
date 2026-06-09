"""DART KR insider transactions backfill — Wave 3.

DART OpenAPI '대량보유 보고' (major holder report) + '임원·주요주주 보유 보고'
endpoints provide KR insider transaction data.

API: https://opendart.fss.or.kr/api/elestock.json (임원·주요주주특정증권등소유상황보고)

Schema (existing): insider_transactions
  market, ticker, trade_date, role, transaction_type, shares, price_per_share

Usage:
    uv run python scripts/dart_insider_backfill.py --years 5
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


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS insider_transactions (
    market           VARCHAR(8) NOT NULL,
    ticker           VARCHAR(16) NOT NULL,
    trade_date       DATE NOT NULL,
    insider_name     VARCHAR(128),
    role             VARCHAR(64),
    transaction_type VARCHAR(32),
    shares           BIGINT,
    price_per_share  DOUBLE PRECISION,
    holding_after    BIGINT,
    source           VARCHAR(32),
    source_id        VARCHAR(64),
    as_of_ts         TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (market, ticker, trade_date, insider_name, transaction_type, shares)
);
CREATE INDEX IF NOT EXISTS ix_insider_kr_ticker
    ON insider_transactions (market, ticker, trade_date);
"""


def fetch_dart_insider(corp_code: str, api_key: str, start: str, end: str) -> list[dict]:
    """Fetch 임원·주요주주특정증권등소유상황보고."""
    url = "https://opendart.fss.or.kr/api/elestock.json"
    try:
        r = httpx.get(url, params={
            "crtfc_key": api_key, "corp_code": corp_code,
            "bgn_de": start, "end_de": end,
        }, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    if data.get("status") != "000":
        return []
    return data.get("list", [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    settings = get_settings()
    api_key = getattr(settings, "dart_api_key", None)
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()
    if not api_key:
        print("DART API key missing"); sys.exit(1)

    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))

    end_d = date.today()
    start_d = end_d - timedelta(days=365 * args.years)
    start_s = start_d.strftime("%Y%m%d")
    end_s = end_d.strftime("%Y%m%d")
    print(f"DART insider backfill :: {start_s} -> {end_s}")

    with session_scope() as s:
        rows = list(s.execute(select(
            Security.ticker, Security.corp_code
        ).where(Security.market == "KR", Security.corp_code.isnot(None),
                  Security.is_active == True)).all())
    if args.limit > 0:
        rows = rows[:args.limit]
    print(f"  {len(rows)} KR tickers with corp_code")

    now = datetime.now(timezone.utc)
    total = 0
    for i, (tk, corp) in enumerate(rows, 1):
        time.sleep(0.4)
        data_rows = fetch_dart_insider(corp, api_key, start_s, end_s)
        if not data_rows:
            continue
        insert_rows = []
        for r in data_rows:
            try:
                # DART elestock returns rcept_dt as 'YYYY-MM-DD'
                trade_str = (r.get("rcept_dt") or "").replace("-", "")
                if not trade_str or len(trade_str) != 8:
                    continue
                td = date(int(trade_str[:4]), int(trade_str[4:6]), int(trade_str[6:8]))
                # sp_stock_lmp_irds_cnt = 변동수량 (+ 매수 / - 매도, comma-separated)
                shares_raw = (r.get("sp_stock_lmp_irds_cnt") or "0").replace(",", "")
                holding_raw = (r.get("sp_stock_lmp_cnt") or "0").replace(",", "")
                try:
                    shares = int(shares_raw) if shares_raw and shares_raw != "-" else 0
                except ValueError:
                    shares = 0
                try:
                    holding_after = int(holding_raw) if holding_raw and holding_raw != "-" else 0
                except ValueError:
                    holding_after = 0
                if shares == 0:
                    continue
                txn_type = "P" if shares > 0 else "S"
                insert_rows.append({
                    "market": "KR", "ticker": tk, "trade_date": td,
                    "insider_name": (r.get("repror") or "")[:128],
                    "role": (r.get("isu_exctv_ofcps") or r.get("isu_exctv_rgist_at") or "")[:64],
                    "transaction_type": txn_type,
                    "shares": abs(shares),
                    "price_per_share": 0.0,   # DART elestock doesn't provide price
                    "holding_after": holding_after,
                    "source": "dart_elestock",
                    "source_id": (r.get("rcept_no") or "")[:64],
                    "as_of_ts": now,
                })
            except (ValueError, KeyError):
                continue
        if insert_rows:
            with eng.begin() as conn:
                conn.execute(text("""
                    INSERT INTO insider_transactions
                    (market, ticker, trade_date, insider_name, role,
                     transaction_type, shares, price_per_share, holding_after,
                     source, source_id, as_of_ts)
                    VALUES (:market, :ticker, :trade_date, :insider_name, :role,
                            :transaction_type, :shares, :price_per_share,
                            :holding_after, :source, :source_id, :as_of_ts)
                    ON CONFLICT DO NOTHING
                """), insert_rows)
            total += len(insert_rows)
        if i % 25 == 0:
            print(f"  [{i}/{len(rows)}] total inserted={total:,}", flush=True)

    print(f"\nDone. Total: {total:,} insider transactions")


if __name__ == "__main__":
    main()
