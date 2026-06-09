"""Forward earnings estimates from yfinance — Wave 3.

yfinance .info dict + .analyst_price_target + .earnings_estimate + .revenue_estimate
provides limited but free forward consensus data.

Schema (new table: forward_estimates):
  market, ticker, as_of_date,
  forward_pe, forward_eps_avg, forward_eps_high, forward_eps_low,
  forward_eps_n_analysts, forward_revenue_avg,
  price_target_mean, price_target_high, price_target_low, price_target_n,
  recommendation_mean, recommendation_n

Usage:
    uv run python scripts/yfinance_forward_estimates.py --limit 500
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from core.db import get_engine, session_scope
from core.models.universe import Security


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS forward_estimates (
    market                  VARCHAR(8) NOT NULL,
    ticker                  VARCHAR(16) NOT NULL,
    as_of_date              DATE NOT NULL,
    forward_pe              DOUBLE PRECISION,
    forward_eps_avg         DOUBLE PRECISION,
    forward_eps_high        DOUBLE PRECISION,
    forward_eps_low         DOUBLE PRECISION,
    forward_eps_n_analysts  INTEGER,
    forward_revenue_avg     DOUBLE PRECISION,
    price_target_mean       DOUBLE PRECISION,
    price_target_high       DOUBLE PRECISION,
    price_target_low        DOUBLE PRECISION,
    price_target_n          INTEGER,
    recommendation_mean     DOUBLE PRECISION,
    recommendation_n        INTEGER,
    as_of_ts                TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (market, ticker, as_of_date)
);
CREATE INDEX IF NOT EXISTS ix_forward_est_ticker
    ON forward_estimates (market, ticker, as_of_date);
"""


def fetch_estimates(ticker: str) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception:
        return None
    out = {
        "forward_pe": info.get("forwardPE"),
        "forward_eps_avg": info.get("forwardEps"),
        "forward_eps_high": None, "forward_eps_low": None,
        "forward_eps_n_analysts": None,
        "forward_revenue_avg": None,
        "price_target_mean": info.get("targetMeanPrice"),
        "price_target_high": info.get("targetHighPrice"),
        "price_target_low": info.get("targetLowPrice"),
        "price_target_n": info.get("numberOfAnalystOpinions"),
        "recommendation_mean": info.get("recommendationMean"),
        "recommendation_n": info.get("numberOfAnalystOpinions"),
    }
    # Earnings estimate detailed table (if available)
    try:
        ee = t.earnings_estimate
        if ee is not None and not ee.empty:
            # Pick "next year" or "+1y" row if present
            for label in ("+1y", "next year", "0y", "current year"):
                if label in ee.index:
                    row = ee.loc[label]
                    out["forward_eps_avg"] = float(row.get("avg") or 0) or out["forward_eps_avg"]
                    out["forward_eps_high"] = float(row.get("high") or 0) or None
                    out["forward_eps_low"] = float(row.get("low") or 0) or None
                    out["forward_eps_n_analysts"] = int(row.get("numberOfAnalysts") or 0) or None
                    break
    except Exception:
        pass
    try:
        re = t.revenue_estimate
        if re is not None and not re.empty:
            for label in ("+1y", "next year", "0y", "current year"):
                if label in re.index:
                    row = re.loc[label]
                    out["forward_revenue_avg"] = float(row.get("avg") or 0) or None
                    break
    except Exception:
        pass
    # Drop None all
    if all(v is None for v in out.values()):
        return None
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all US tickers")
    args = ap.parse_args()

    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print("forward_estimates table ensured.")

    with session_scope() as s:
        rows = list(s.execute(select(Security.ticker).where(
            Security.market == "US", Security.is_active == True
        )).all())
    tickers = [r[0] for r in rows]
    if args.limit > 0:
        tickers = tickers[:args.limit]
    print(f"Processing {len(tickers)} US tickers...")

    today = date.today()
    now = datetime.now(timezone.utc)
    total_ok, total_fail = 0, 0
    for tk in tickers:
        est = fetch_estimates(tk)
        if not est:
            total_fail += 1
            time.sleep(0.3)
            continue
        with eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO forward_estimates
                (market, ticker, as_of_date, forward_pe, forward_eps_avg,
                 forward_eps_high, forward_eps_low, forward_eps_n_analysts,
                 forward_revenue_avg, price_target_mean, price_target_high,
                 price_target_low, price_target_n, recommendation_mean,
                 recommendation_n, as_of_ts)
                VALUES ('US', :ticker, :today, :fpe, :feavg, :fehigh, :felow, :fen,
                        :frev, :ptm, :pth, :ptl, :ptn, :rcm, :rcn, :ats)
                ON CONFLICT (market, ticker, as_of_date) DO UPDATE SET
                    forward_pe = EXCLUDED.forward_pe,
                    forward_eps_avg = EXCLUDED.forward_eps_avg,
                    price_target_mean = EXCLUDED.price_target_mean,
                    recommendation_mean = EXCLUDED.recommendation_mean
            """), {
                "ticker": tk, "today": today,
                "fpe": est["forward_pe"], "feavg": est["forward_eps_avg"],
                "fehigh": est["forward_eps_high"], "felow": est["forward_eps_low"],
                "fen": est["forward_eps_n_analysts"],
                "frev": est["forward_revenue_avg"],
                "ptm": est["price_target_mean"], "pth": est["price_target_high"],
                "ptl": est["price_target_low"], "ptn": est["price_target_n"],
                "rcm": est["recommendation_mean"], "rcn": est["recommendation_n"],
                "ats": now,
            })
        total_ok += 1
        time.sleep(0.3)   # yfinance rate-limit gentle
        if total_ok % 25 == 0:
            print(f"  ok={total_ok}/{len(tickers)} fail={total_fail}", flush=True)

    print(f"\nDone. ok={total_ok}/{len(tickers)} fail={total_fail}")


if __name__ == "__main__":
    main()
