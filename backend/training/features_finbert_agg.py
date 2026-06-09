"""FinBERT aggregator features — Wave 3.

Reads from `news_finbert_score` and produces per (market, ticker, date):

  - finbert_pos_avg_7d / _30d
  - finbert_neg_avg_7d / _30d
  - finbert_net_sent_7d / _30d   (pos - neg)
  - finbert_sent_volatility_30d  (std of net sentiment)
  - finbert_strong_pos_count_30d (count where positive > 0.3)
  - finbert_strong_neg_count_30d (count where negative > 0.3)
  - finbert_sent_momentum_7d     (7d avg - 30d avg)
  - finbert_label_pos_ratio_7d   (label='positive' count / total)

Total: 12 features per ticker.
"""
from __future__ import annotations

from datetime import date as DateType, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session


def compute_finbert_features(session: Session, market: str, ticker: str,
                                dates: pd.DatetimeIndex) -> pd.DataFrame:
    cols = [
        "finbert_pos_avg_7d", "finbert_pos_avg_30d",
        "finbert_neg_avg_7d", "finbert_neg_avg_30d",
        "finbert_net_sent_7d", "finbert_net_sent_30d",
        "finbert_sent_vol_30d", "finbert_strong_pos_30d", "finbert_strong_neg_30d",
        "finbert_sent_momentum", "finbert_label_pos_ratio_7d", "finbert_n_articles_7d",
    ]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)

    start = pd.Timestamp(dates.min()).date() - timedelta(days=45)
    end = pd.Timestamp(dates.max()).date()
    try:
        with session.begin_nested():
            rows = session.execute(text("""
                SELECT fs.article_published_ts AT TIME ZONE 'UTC' AS ts,
                       fs.finbert_positive, fs.finbert_negative,
                       fs.finbert_label
                  FROM news_finbert_score fs
                  JOIN news_ticker_mentions m
                    ON m.article_id = fs.article_id
                   AND m.article_published_ts = fs.article_published_ts
                 WHERE m.market = :market AND m.ticker = :ticker
                   AND fs.article_published_ts >= :start
                   AND fs.article_published_ts <= :end
            """), {"market": market, "ticker": ticker,
                    "start": pd.Timestamp(start),
                    "end": pd.Timestamp(end) + pd.Timedelta(days=1)}).all()
    except Exception:
        return pd.DataFrame(0.0, index=dates, columns=cols)

    if not rows:
        return pd.DataFrame(0.0, index=dates, columns=cols)

    df = pd.DataFrame(rows, columns=["ts", "pos", "neg", "label"])
    df["ts"] = pd.to_datetime(df["ts"]).dt.normalize()
    df["pos"] = df["pos"].astype(float); df["neg"] = df["neg"].astype(float)
    df["net"] = df["pos"] - df["neg"]
    df["strong_pos"] = (df["pos"] > 0.3).astype(int)
    df["strong_neg"] = (df["neg"] > 0.3).astype(int)
    df["lbl_pos"] = (df["label"] == "positive").astype(int)

    grp = df.groupby("ts")
    daily = pd.DataFrame({
        "pos_mean": grp["pos"].mean(),
        "neg_mean": grp["neg"].mean(),
        "net_mean": grp["net"].mean(),
        "strong_pos_sum": grp["strong_pos"].sum(),
        "strong_neg_sum": grp["strong_neg"].sum(),
        "lbl_pos_sum": grp["lbl_pos"].sum(),
        "count": grp.size(),
    }).sort_index()
    full = pd.date_range(daily.index.min(), dates.max(), freq="D")
    daily = daily.reindex(full).fillna(0)

    out = pd.DataFrame(index=daily.index)
    out["finbert_pos_avg_7d"] = daily["pos_mean"].rolling(7, min_periods=1).mean()
    out["finbert_pos_avg_30d"] = daily["pos_mean"].rolling(30, min_periods=1).mean()
    out["finbert_neg_avg_7d"] = daily["neg_mean"].rolling(7, min_periods=1).mean()
    out["finbert_neg_avg_30d"] = daily["neg_mean"].rolling(30, min_periods=1).mean()
    out["finbert_net_sent_7d"] = daily["net_mean"].rolling(7, min_periods=1).mean()
    out["finbert_net_sent_30d"] = daily["net_mean"].rolling(30, min_periods=1).mean()
    out["finbert_sent_vol_30d"] = daily["net_mean"].rolling(30, min_periods=1).std().fillna(0)
    out["finbert_strong_pos_30d"] = daily["strong_pos_sum"].rolling(30, min_periods=1).sum()
    out["finbert_strong_neg_30d"] = daily["strong_neg_sum"].rolling(30, min_periods=1).sum()
    out["finbert_sent_momentum"] = out["finbert_net_sent_7d"] - out["finbert_net_sent_30d"]
    total_7d = daily["count"].rolling(7, min_periods=1).sum().replace(0, np.nan)
    out["finbert_label_pos_ratio_7d"] = (
        daily["lbl_pos_sum"].rolling(7, min_periods=1).sum() / total_7d
    ).fillna(0)
    out["finbert_n_articles_7d"] = daily["count"].rolling(7, min_periods=1).sum()

    return out.reindex(dates).fillna(0.0)
