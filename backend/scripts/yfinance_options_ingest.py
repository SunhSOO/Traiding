"""yfinance options chain ingest — Wave 2.

Pull options chain (front-month, nearest 2 expiries) for top liquid US
tickers and compute per-ticker daily aggregates:

  - put_call_volume_ratio
  - put_call_oi_ratio
  - iv_atm_mean (avg of nearest-the-money implied vol)
  - iv_skew (25Δ put IV - 25Δ call IV, proxy via OTM strikes)
  - iv_term_slope (back-month - front-month IV)
  - unusual_activity_count (contracts where volume > 3× open_interest)

Schema (new table: options_aggregates):
  ts, market, ticker, pc_vol_ratio, pc_oi_ratio, iv_atm, iv_skew,
  iv_term_slope, unusual_count

Usage:
    uv run python scripts/yfinance_options_ingest.py --tickers AAPL,MSFT,NVDA
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from core.db import get_engine, session_scope
from core.models.universe import Security


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS options_aggregates (
    ts             DATE NOT NULL,
    market         VARCHAR(8) NOT NULL DEFAULT 'US',
    ticker         VARCHAR(16) NOT NULL,
    pc_vol_ratio   DOUBLE PRECISION,
    pc_oi_ratio    DOUBLE PRECISION,
    iv_atm         DOUBLE PRECISION,
    iv_skew        DOUBLE PRECISION,
    iv_term_slope  DOUBLE PRECISION,
    unusual_count  INTEGER,
    as_of_ts       TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (ts, market, ticker)
);
CREATE INDEX IF NOT EXISTS ix_options_agg_ticker
    ON options_aggregates (market, ticker, ts);
"""


def ingest_ticker(ticker: str, today: date) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        expiries = t.options
    except Exception:
        return None
    if not expiries:
        return None
    front = expiries[0]
    back = expiries[1] if len(expiries) > 1 else None
    try:
        chain_f = t.option_chain(front)
        chain_b = t.option_chain(back) if back else None
    except Exception:
        return None

    calls_f, puts_f = chain_f.calls, chain_f.puts
    if calls_f.empty or puts_f.empty:
        return None

    try:
        spot = t.info.get("regularMarketPrice") or t.history(period="1d")["Close"].iloc[-1]
    except Exception:
        return None

    pc_vol = puts_f["volume"].fillna(0).sum() / max(1, calls_f["volume"].fillna(0).sum())
    pc_oi = puts_f["openInterest"].fillna(0).sum() / max(1, calls_f["openInterest"].fillna(0).sum())

    # ATM IV
    atm_call = calls_f.iloc[(calls_f["strike"] - spot).abs().idxmin()]
    atm_put = puts_f.iloc[(puts_f["strike"] - spot).abs().idxmin()]
    iv_atm = float((atm_call["impliedVolatility"] + atm_put["impliedVolatility"]) / 2)

    # IV skew via 25Δ proxy: pick OTM put ~10% OTM, OTM call ~10% OTM
    otm_p = puts_f[puts_f["strike"] < spot * 0.9].tail(1)
    otm_c = calls_f[calls_f["strike"] > spot * 1.1].head(1)
    iv_skew = float("nan")
    if not otm_p.empty and not otm_c.empty:
        iv_skew = float(otm_p["impliedVolatility"].iloc[0] - otm_c["impliedVolatility"].iloc[0])

    # Term slope
    iv_term = float("nan")
    if chain_b and not chain_b.calls.empty:
        b_atm = chain_b.calls.iloc[(chain_b.calls["strike"] - spot).abs().idxmin()]
        iv_term = float(b_atm["impliedVolatility"] - atm_call["impliedVolatility"])

    # Unusual activity: contracts where volume > 3× OI
    combined = pd.concat([calls_f, puts_f])
    unusual = (combined["volume"].fillna(0) > 3 * combined["openInterest"].fillna(1)).sum()

    return {
        "ts": today, "market": "US", "ticker": ticker,
        "pc_vol_ratio": float(pc_vol), "pc_oi_ratio": float(pc_oi),
        "iv_atm": iv_atm, "iv_skew": iv_skew, "iv_term_slope": iv_term,
        "unusual_count": int(unusual),
        "as_of_ts": datetime.now(timezone.utc),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default="",
                    help="Comma-separated tickers; default = top 100 from securities")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print("options_aggregates table ensured.")

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    else:
        with session_scope() as s:
            rows = list(s.execute(
                select(Security.ticker)
                .where(Security.market == "US", Security.is_active == True)
                .limit(args.limit)
            ).all())
        tickers = [r[0] for r in rows]

    today = date.today()
    total_ok = 0
    for tk in tickers:
        try:
            d = ingest_ticker(tk, today)
            if d:
                with eng.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO options_aggregates
                        (ts, market, ticker, pc_vol_ratio, pc_oi_ratio,
                         iv_atm, iv_skew, iv_term_slope, unusual_count, as_of_ts)
                        VALUES (:ts, :market, :ticker, :pc_vol_ratio, :pc_oi_ratio,
                                :iv_atm, :iv_skew, :iv_term_slope, :unusual_count, :as_of_ts)
                        ON CONFLICT (ts, market, ticker) DO UPDATE SET
                            pc_vol_ratio = EXCLUDED.pc_vol_ratio,
                            iv_atm = EXCLUDED.iv_atm
                    """), d)
                total_ok += 1
        except Exception as e:
            print(f"  {tk}: FAIL {type(e).__name__}: {e}")
        time.sleep(0.4)
        if total_ok % 20 == 0:
            print(f"  ok={total_ok}/{len(tickers)}", flush=True)

    print(f"\nDone. {total_ok}/{len(tickers)} tickers.")


if __name__ == "__main__":
    main()
