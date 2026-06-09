"""Sentiment Surprise feature — Wave 3.

For each earnings_call_sentiment row, compute:
  - subsequent 1d / 5d / 21d return after filing_date
  - sentiment surprise = (actual return - expected return based on sentiment)

Logic:
  - Group by ticker: compute linear regression of (sentiment -> 21d return)
    over rolling 12-quarter window
  - Surprise = residual (actual - predicted)
  - Strong positive surprise = market reaction stronger than sentiment suggested
  - Strong negative surprise = market underwhelmed even though sentiment positive

Writes to: earnings_sentiment_surprise table.

Aggregator -> features_finbert_agg.py would join this for per-ticker
'earnings_surprise_avg_30d' feature.

Usage:
    uv run python scripts/compute_sentiment_surprise.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text, select
from core.db import get_engine, session_scope
from core.models.prices import DailyPrice


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS earnings_sentiment_surprise (
    market               VARCHAR(8) NOT NULL,
    ticker               VARCHAR(16) NOT NULL,
    filing_date          DATE NOT NULL,
    ret_1d_after         DOUBLE PRECISION,
    ret_5d_after         DOUBLE PRECISION,
    ret_21d_after        DOUBLE PRECISION,
    sentiment            DOUBLE PRECISION,
    expected_ret         DOUBLE PRECISION,
    surprise_21d         DOUBLE PRECISION,
    abs_surprise_z       DOUBLE PRECISION,
    as_of_ts             TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (market, ticker, filing_date)
);
CREATE INDEX IF NOT EXISTS ix_surprise_ticker
    ON earnings_sentiment_surprise (market, ticker, filing_date);
"""


def main() -> None:
    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print("earnings_sentiment_surprise table ensured.")

    # Load earnings sentiment
    with session_scope() as s:
        rows = list(s.execute(text("""
            SELECT market, ticker, filing_date,
                   COALESCE(sliding_sent_mean, transcript_sent_finbert) AS sent
              FROM earnings_call_sentiment
             WHERE COALESCE(sliding_sent_mean, transcript_sent_finbert) IS NOT NULL
             ORDER BY ticker, filing_date
        """)).all())
    if not rows:
        print("No earnings sentiment rows"); return
    df = pd.DataFrame(rows, columns=["market", "ticker", "fd", "sent"])
    df["fd"] = pd.to_datetime(df["fd"]).dt.date
    print(f"earnings calls with sentiment: {len(df):,}")

    # Load price panel for tickers
    tickers = df["ticker"].unique().tolist()
    with session_scope() as s:
        px = list(s.execute(select(
            DailyPrice.ticker, DailyPrice.trade_date, DailyPrice.close
        ).where(DailyPrice.market == "US",
                  DailyPrice.ticker.in_(tickers))).all())
    px_df = pd.DataFrame(px, columns=["ticker", "ts", "close"])
    px_df["close"] = px_df["close"].astype(float)
    px_df["ts"] = pd.to_datetime(px_df["ts"])
    px_pivot = px_df.pivot(index="ts", columns="ticker", values="close").sort_index()

    # Compute returns after filing date
    results = []
    for _, r in df.iterrows():
        tk = r["ticker"]; fd = r["fd"]; sent = r["sent"]
        if tk not in px_pivot.columns:
            continue
        series = px_pivot[tk].dropna()
        fd_ts = pd.Timestamp(fd)
        # Find first trading day >= fd
        future = series[series.index >= fd_ts]
        if len(future) < 22:
            continue
        p0 = future.iloc[0]
        p1 = future.iloc[1] if len(future) > 1 else np.nan
        p5 = future.iloc[5] if len(future) > 5 else np.nan
        p21 = future.iloc[21] if len(future) > 21 else np.nan
        r1 = (p1 / p0 - 1) if np.isfinite(p1) else np.nan
        r5 = (p5 / p0 - 1) if np.isfinite(p5) else np.nan
        r21 = (p21 / p0 - 1) if np.isfinite(p21) else np.nan
        results.append({"market": "US", "ticker": tk, "fd": fd,
                          "sent": float(sent), "r1": r1, "r5": r5, "r21": r21})

    results_df = pd.DataFrame(results)
    if results_df.empty:
        print("No price overlap"); return

    # Linear regression sentiment -> r21 over all rows
    valid = results_df.dropna(subset=["sent", "r21"])
    if len(valid) < 30:
        print(f"too few valid samples: {len(valid)}"); return
    # Global beta
    from scipy.stats import linregress
    slope, intercept, rval, _, _ = linregress(valid["sent"], valid["r21"])
    print(f"sentiment -> 21d return: slope={slope:+.4f} intercept={intercept:+.4f} r={rval:+.3f}")
    results_df["expected_r21"] = intercept + slope * results_df["sent"]
    results_df["surprise"] = results_df["r21"] - results_df["expected_r21"]
    surprise_std = results_df["surprise"].std(skipna=True) or 1e-9
    results_df["surprise_z"] = results_df["surprise"] / surprise_std

    # Persist
    now = datetime.now(timezone.utc)
    insert_rows = []
    for _, r in results_df.iterrows():
        if not np.isfinite(r["r21"]):
            continue
        insert_rows.append({
            "market": "US", "ticker": r["ticker"], "fd": r["fd"],
            "r1": r["r1"] if np.isfinite(r["r1"]) else None,
            "r5": r["r5"] if np.isfinite(r["r5"]) else None,
            "r21": r["r21"] if np.isfinite(r["r21"]) else None,
            "sent": r["sent"], "expected": r["expected_r21"],
            "surprise": r["surprise"] if np.isfinite(r["surprise"]) else None,
            "abs_z": abs(r["surprise_z"]) if np.isfinite(r["surprise_z"]) else None,
            "ats": now,
        })
    with eng.begin() as conn:
        conn.execute(text("""
            INSERT INTO earnings_sentiment_surprise
            (market, ticker, filing_date, ret_1d_after, ret_5d_after, ret_21d_after,
             sentiment, expected_ret, surprise_21d, abs_surprise_z, as_of_ts)
            VALUES (:market, :ticker, :fd, :r1, :r5, :r21,
                    :sent, :expected, :surprise, :abs_z, :ats)
            ON CONFLICT (market, ticker, filing_date) DO UPDATE SET
                ret_21d_after = EXCLUDED.ret_21d_after,
                surprise_21d = EXCLUDED.surprise_21d,
                abs_surprise_z = EXCLUDED.abs_surprise_z
        """), insert_rows)
    print(f"\nInserted {len(insert_rows)} surprise rows")
    print(f"  abs surprise z mean: {results_df['surprise_z'].abs().mean():.3f}")
    print(f"  pct with abs surprise z > 1: {(results_df['surprise_z'].abs() > 1).mean()*100:.1f}%")
    print(f"  pct with abs surprise z > 2: {(results_df['surprise_z'].abs() > 2).mean()*100:.1f}%")


if __name__ == "__main__":
    main()
