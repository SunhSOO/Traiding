"""FINRA Consolidated Short Interest backfill (settled bi-monthly positioning).

This is short *INTEREST* (settled shares held short, published ~twice a month)
— a different signal from the daily short *VOLUME* the project already ingested
and found to be MM-hedge noise (augment_shortvol.py). Short-interest %/days-to-
cover is a documented crowded-short / squeeze cross-sectional predictor.

Source: FINRA Query API — genuinely free, NO key/auth. Verified: real history
back to ~2017-12 (e.g. AAPL 2017-12-29 → present), bi-monthly settlement dates.
    POST https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest
    body {compareFilters:[{compareType:EQUAL, fieldName:symbolCode, fieldValue:TK}],
          limit:5000}   -> CSV text (one ticker's full ~200-row history per call)

Look-ahead safety: FINRA publishes ~8-9 business days AFTER the settlement date,
so a settlement value is not knowable until then. Storage keeps settlement_date;
the feature layer lags availability (settlement + ~14 calendar days).

Usage:
    uv run python scripts/finra_short_interest_backfill.py [--limit N] [--tickers AAPL,MSFT]
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text

from core.db import get_engine, session_scope
from core.models.universe import Security

FINRA_URL = ("https://api.finra.org/data/group/otcMarket/name/"
             "consolidatedShortInterest")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS short_interest (
    market                VARCHAR(8) NOT NULL DEFAULT 'US',
    ticker                VARCHAR(16) NOT NULL,
    settlement_date       DATE NOT NULL,
    short_interest_shares DOUBLE PRECISION,
    avg_daily_volume      DOUBLE PRECISION,
    days_to_cover         DOUBLE PRECISION,
    prev_short_interest   DOUBLE PRECISION,
    change_pct            DOUBLE PRECISION,
    as_of_ts              TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (market, ticker, settlement_date)
);
CREATE INDEX IF NOT EXISTS ix_short_interest_ticker
    ON short_interest (market, ticker, settlement_date);
"""


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_short_interest(ticker: str, *, client: httpx.Client) -> list[dict]:
    """Full settled short-interest history for one ticker (≤5000 rows)."""
    payload = {
        "compareFilters": [
            {"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": ticker},
        ],
        "limit": 5000,
    }
    try:
        r = client.post(FINRA_URL, json=payload, timeout=60)
        r.raise_for_status()
    except Exception as e:
        print(f"  {ticker}: FAIL {type(e).__name__}: {e}", flush=True)
        return []
    text_body = r.text
    if not text_body.strip():
        return []
    rows = list(csv.DictReader(io.StringIO(text_body)))
    out = []
    for row in rows:
        sd = row.get("settlementDate")
        if not sd:
            continue
        try:
            settle = date.fromisoformat(sd[:10])
        except ValueError:
            continue
        out.append({
            "ticker": ticker,
            "settlement_date": settle,
            "short_interest_shares": _to_float(row.get("currentShortPositionQuantity")),
            "avg_daily_volume": _to_float(row.get("averageDailyVolumeQuantity")),
            "days_to_cover": _to_float(row.get("daysToCoverQuantity")),
            "prev_short_interest": _to_float(row.get("previousShortPositionQuantity")),
            "change_pct": _to_float(row.get("changePercent")),
        })
    return out


def run_short_interest_backfill(limit: int = 0,
                                 tickers: list[str] | None = None) -> dict:
    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))

    if tickers is None:
        with session_scope() as s:
            rows = list(s.execute(select(Security.ticker).where(
                Security.market == "US", Security.is_active == True
            )).all())
        tickers = [r[0] for r in rows]
    if limit > 0:
        tickers = tickers[:limit]
    print(f"FINRA short-interest backfill :: {len(tickers)} US tickers", flush=True)

    now = datetime.now(timezone.utc)
    total_ok, total_rows, total_fail = 0, 0, 0
    with httpx.Client() as client:
        for tk in tickers:
            recs = fetch_short_interest(tk, client=client)
            if not recs:
                total_fail += 1
                time.sleep(0.15)
                continue
            with eng.begin() as conn:
                for rec in recs:
                    conn.execute(text("""
                        INSERT INTO short_interest
                        (market, ticker, settlement_date, short_interest_shares,
                         avg_daily_volume, days_to_cover, prev_short_interest,
                         change_pct, as_of_ts)
                        VALUES ('US', :ticker, :sd, :si, :adv, :dtc, :psi, :cpct, :ats)
                        ON CONFLICT (market, ticker, settlement_date) DO UPDATE SET
                            short_interest_shares = EXCLUDED.short_interest_shares,
                            avg_daily_volume = EXCLUDED.avg_daily_volume,
                            days_to_cover = EXCLUDED.days_to_cover,
                            prev_short_interest = EXCLUDED.prev_short_interest,
                            change_pct = EXCLUDED.change_pct
                    """), {
                        "ticker": tk, "sd": rec["settlement_date"],
                        "si": rec["short_interest_shares"],
                        "adv": rec["avg_daily_volume"],
                        "dtc": rec["days_to_cover"],
                        "psi": rec["prev_short_interest"],
                        "cpct": rec["change_pct"], "ats": now,
                    })
            total_ok += 1
            total_rows += len(recs)
            time.sleep(0.15)
            if total_ok % 50 == 0:
                print(f"  ok={total_ok}/{len(tickers)} rows={total_rows} fail={total_fail}", flush=True)

    print(f"\nDone. ok={total_ok}/{len(tickers)} rows={total_rows} fail={total_fail}", flush=True)
    return {"ok": total_ok, "rows": total_rows, "fail": total_fail, "total": len(tickers)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all US tickers")
    ap.add_argument("--tickers", type=str, default=None, help="comma-separated override")
    args = ap.parse_args()
    tk = args.tickers.split(",") if args.tickers else None
    run_short_interest_backfill(limit=args.limit, tickers=tk)


if __name__ == "__main__":
    main()
