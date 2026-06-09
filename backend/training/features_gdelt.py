"""GDELT V2Tone aggregator features — Wave 1.

Reads `gdelt_aux` (article_id, market, ticker, tone, article_published_ts)
and produces per (market, ticker, date) features:

  - gdelt_tone_avg_7d, gdelt_tone_avg_30d
  - gdelt_tone_std_30d
  - gdelt_tone_momentum (7d_avg - 30d_avg)
  - gdelt_tone_pos_count_7d  (tone > +3)
  - gdelt_tone_neg_count_7d  (tone < -3)
  - gdelt_mention_count_7d
"""
from __future__ import annotations

from datetime import date as DateType, timedelta

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session


def load_gdelt_tones(
    session: Session, market: str, tickers: list[str],
    start: DateType, end: DateType,
) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame(columns=["ticker", "ts", "tone"])
    # Use ANY for tickers list
    q = text("""
        SELECT ticker, article_published_ts AT TIME ZONE 'UTC' AS ts, tone
          FROM gdelt_aux
         WHERE market = :market
           AND ticker = ANY(:tickers)
           AND article_published_ts >= :start
           AND article_published_ts < :end
    """)
    rows = session.execute(q, {
        "market": market,
        "tickers": tickers,
        "start": pd.Timestamp(start),
        "end": pd.Timestamp(end) + pd.Timedelta(days=1),
    }).all()
    if not rows:
        return pd.DataFrame(columns=["ticker", "ts", "tone"])
    df = pd.DataFrame(rows, columns=["ticker", "ts", "tone"])
    df["ts"] = pd.to_datetime(df["ts"]).dt.normalize()
    return df


def compute_gdelt_features(
    session: Session, market: str, ticker: str, dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Compute per-date GDELT V2Tone features for a single ticker."""
    if len(dates) == 0:
        return pd.DataFrame()

    start = pd.Timestamp(dates.min()).date() - timedelta(days=35)
    end = pd.Timestamp(dates.max()).date()
    raw = load_gdelt_tones(session, market, [ticker], start, end)

    cols = [
        "gdelt_tone_avg_7d", "gdelt_tone_avg_30d", "gdelt_tone_std_30d",
        "gdelt_tone_momentum", "gdelt_pos_count_7d", "gdelt_neg_count_7d",
        "gdelt_mention_count_7d",
    ]
    if raw.empty:
        out = pd.DataFrame(0.0, index=dates, columns=cols)
        return out

    raw = raw[raw["ticker"] == ticker].copy()
    raw_grp = raw.groupby("ts")["tone"]
    daily = pd.DataFrame({
        "tone_mean": raw_grp.mean(),
        "tone_count": raw_grp.size(),
        "pos_count": raw.assign(p=raw["tone"] > 3.0).groupby("ts")["p"].sum(),
        "neg_count": raw.assign(n=raw["tone"] < -3.0).groupby("ts")["n"].sum(),
    }).sort_index()
    full_idx = pd.date_range(daily.index.min(), dates.max(), freq="D")
    daily = daily.reindex(full_idx).fillna(0.0)

    out = pd.DataFrame(index=daily.index)
    out["gdelt_tone_avg_7d"] = daily["tone_mean"].rolling(7, min_periods=1).mean()
    out["gdelt_tone_avg_30d"] = daily["tone_mean"].rolling(30, min_periods=1).mean()
    out["gdelt_tone_std_30d"] = daily["tone_mean"].rolling(30, min_periods=1).std().fillna(0.0)
    out["gdelt_tone_momentum"] = out["gdelt_tone_avg_7d"] - out["gdelt_tone_avg_30d"]
    out["gdelt_pos_count_7d"] = daily["pos_count"].rolling(7, min_periods=1).sum()
    out["gdelt_neg_count_7d"] = daily["neg_count"].rolling(7, min_periods=1).sum()
    out["gdelt_mention_count_7d"] = daily["tone_count"].rolling(7, min_periods=1).sum()

    out = out.reindex(dates).fillna(0.0)
    return out
