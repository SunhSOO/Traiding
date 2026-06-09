"""Alt data features — Wave 2.

Reads from new ingest tables (created by alt_data_ingest.py and friends).

Outputs per (ticker, date):

  - Short Interest (5):    short_ratio, short_ratio_5d, short_ratio_z30,
                           short_ratio_chg_7d, short_squeeze_score
  - Options (6):           pc_vol_ratio, pc_oi_ratio, iv_atm, iv_skew,
                           iv_term_slope, unusual_count
  - Wikipedia (3):         wiki_views_7d, wiki_views_z_30d, wiki_views_chg_7d
  - Google Trends (3):     trends_interest_7d, trends_z_30d, trends_chg_7d
  - Reddit (3):            reddit_mentions_7d, reddit_score_avg_7d, reddit_z_30d
  - Patents (3):           patents_count_90d, patents_avg_cites_90d, patents_chg_yoy
  - 13F (3):               inst_filing_count_q, inst_filing_count_q_chg
  - GCAM (4):              gcam_fear_7d, gcam_anger_7d, gcam_econ_neg_7d,
                           gcam_polarity_30d

All return 0/NaN if source table empty.
"""
from __future__ import annotations

from datetime import date as DateType, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session


def _safe_table_query(session: Session, sql: str, params: dict) -> list:
    """Run query inside a savepoint so a failure (e.g. missing table)
    doesn't abort the outer transaction."""
    try:
        with session.begin_nested():
            return session.execute(text(sql), params).all()
    except Exception:
        return []


def compute_short_interest_features(session, market, ticker, dates):
    cols = ["short_ratio", "short_ratio_5d", "short_ratio_z30",
            "short_ratio_chg_7d", "short_squeeze_score"]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)
    start = pd.Timestamp(dates.min()).date() - timedelta(days=60)
    end = pd.Timestamp(dates.max()).date()
    rows = _safe_table_query(session, """
        SELECT ts, short_volume, total_volume, short_ratio
          FROM short_volume_daily
         WHERE market = :market AND ticker = :ticker
           AND ts >= :start AND ts <= :end
         ORDER BY ts
    """, {"market": market, "ticker": ticker, "start": start, "end": end})
    if not rows:
        return pd.DataFrame(0.0, index=dates, columns=cols)
    df = pd.DataFrame(rows, columns=["ts", "sv", "tv", "sr"])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    out = pd.DataFrame(index=df.index)
    out["short_ratio"] = df["sr"].astype(float)
    out["short_ratio_5d"] = df["sr"].rolling(5, min_periods=1).mean()
    mean30 = df["sr"].rolling(30, min_periods=5).mean()
    std30 = df["sr"].rolling(30, min_periods=5).std().replace(0, np.nan)
    out["short_ratio_z30"] = (df["sr"] - mean30) / std30
    out["short_ratio_chg_7d"] = df["sr"] - df["sr"].shift(7)
    out["short_squeeze_score"] = (
        out["short_ratio_z30"].fillna(0) * out["short_ratio_5d"].fillna(0)
    )
    return out.reindex(dates).fillna(method="ffill").fillna(0.0)


def compute_options_features(session, market, ticker, dates):
    cols = ["pc_vol_ratio", "pc_oi_ratio", "iv_atm", "iv_skew",
            "iv_term_slope", "unusual_count"]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)
    start = pd.Timestamp(dates.min()).date() - timedelta(days=30)
    end = pd.Timestamp(dates.max()).date()
    rows = _safe_table_query(session, """
        SELECT ts, pc_vol_ratio, pc_oi_ratio, iv_atm, iv_skew,
               iv_term_slope, unusual_count
          FROM options_aggregates
         WHERE market = :market AND ticker = :ticker
           AND ts >= :start AND ts <= :end
    """, {"market": market, "ticker": ticker, "start": start, "end": end})
    if not rows:
        return pd.DataFrame(0.0, index=dates, columns=cols)
    df = pd.DataFrame(rows, columns=["ts"] + cols)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    return df.reindex(dates).fillna(method="ffill").fillna(0.0)


def compute_wiki_features(session, market, ticker, dates):
    cols = ["wiki_views_7d", "wiki_views_z_30d", "wiki_views_chg_7d"]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)
    start = pd.Timestamp(dates.min()).date() - timedelta(days=60)
    end = pd.Timestamp(dates.max()).date()
    rows = _safe_table_query(session, """
        SELECT ts, views FROM wiki_pageviews
         WHERE market = :market AND ticker = :ticker
           AND ts >= :start AND ts <= :end ORDER BY ts
    """, {"market": market, "ticker": ticker, "start": start, "end": end})
    if not rows:
        return pd.DataFrame(0.0, index=dates, columns=cols)
    df = pd.DataFrame(rows, columns=["ts", "views"])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    full = pd.date_range(df.index.min(), dates.max(), freq="D")
    df = df.reindex(full).fillna(0)
    out = pd.DataFrame(index=df.index)
    out["wiki_views_7d"] = df["views"].rolling(7, min_periods=1).mean()
    mean30 = df["views"].rolling(30, min_periods=5).mean()
    std30 = df["views"].rolling(30, min_periods=5).std().replace(0, np.nan)
    out["wiki_views_z_30d"] = (df["views"] - mean30) / std30
    out["wiki_views_chg_7d"] = df["views"] - df["views"].shift(7)
    return out.reindex(dates).fillna(0.0)


def compute_trends_features(session, market, ticker, dates):
    cols = ["trends_interest_7d", "trends_z_30d", "trends_chg_7d"]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)
    start = pd.Timestamp(dates.min()).date() - timedelta(days=60)
    end = pd.Timestamp(dates.max()).date()
    rows = _safe_table_query(session, """
        SELECT ts, AVG(interest) AS interest
          FROM google_trends
         WHERE market = :market AND ticker = :ticker
           AND ts >= :start AND ts <= :end
         GROUP BY ts ORDER BY ts
    """, {"market": market, "ticker": ticker, "start": start, "end": end})
    if not rows:
        return pd.DataFrame(0.0, index=dates, columns=cols)
    df = pd.DataFrame(rows, columns=["ts", "interest"])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    full = pd.date_range(df.index.min(), dates.max(), freq="D")
    df = df.reindex(full).fillna(method="ffill").fillna(0)
    out = pd.DataFrame(index=df.index)
    out["trends_interest_7d"] = df["interest"].rolling(7, min_periods=1).mean()
    mean30 = df["interest"].rolling(30, min_periods=5).mean()
    std30 = df["interest"].rolling(30, min_periods=5).std().replace(0, np.nan)
    out["trends_z_30d"] = (df["interest"] - mean30) / std30
    out["trends_chg_7d"] = df["interest"] - df["interest"].shift(7)
    return out.reindex(dates).fillna(0.0)


def compute_reddit_features(session, market, ticker, dates):
    cols = ["reddit_mentions_7d", "reddit_score_avg_7d", "reddit_z_30d"]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)
    start = pd.Timestamp(dates.min()).date() - timedelta(days=60)
    end = pd.Timestamp(dates.max()).date()
    rows = _safe_table_query(session, """
        SELECT ts, mention_count, avg_score
          FROM reddit_mentions
         WHERE market = :market AND ticker = :ticker
           AND ts >= :start AND ts <= :end ORDER BY ts
    """, {"market": market, "ticker": ticker, "start": start, "end": end})
    if not rows:
        return pd.DataFrame(0.0, index=dates, columns=cols)
    df = pd.DataFrame(rows, columns=["ts", "mc", "score"])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    full = pd.date_range(df.index.min(), dates.max(), freq="D")
    df = df.reindex(full).fillna(0)
    out = pd.DataFrame(index=df.index)
    out["reddit_mentions_7d"] = df["mc"].rolling(7, min_periods=1).sum()
    out["reddit_score_avg_7d"] = df["score"].rolling(7, min_periods=1).mean()
    mean30 = df["mc"].rolling(30, min_periods=5).mean()
    std30 = df["mc"].rolling(30, min_periods=5).std().replace(0, np.nan)
    out["reddit_z_30d"] = (df["mc"] - mean30) / std30
    return out.reindex(dates).fillna(0.0)


def compute_patents_features(session, market, ticker, dates):
    cols = ["patents_count_90d", "patents_avg_cites_90d", "patents_chg_yoy"]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)
    start = pd.Timestamp(dates.min()).date() - timedelta(days=400)
    end = pd.Timestamp(dates.max()).date()
    rows = _safe_table_query(session, """
        SELECT filing_date, count, avg_citations FROM patent_filings
         WHERE market = :market AND ticker = :ticker
           AND filing_date >= :start AND filing_date <= :end
         ORDER BY filing_date
    """, {"market": market, "ticker": ticker, "start": start, "end": end})
    if not rows:
        return pd.DataFrame(0.0, index=dates, columns=cols)
    df = pd.DataFrame(rows, columns=["ts", "count", "cites"])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    full = pd.date_range(df.index.min(), dates.max(), freq="D")
    df = df.reindex(full).fillna(0)
    out = pd.DataFrame(index=df.index)
    out["patents_count_90d"] = df["count"].rolling(90, min_periods=1).sum()
    out["patents_avg_cites_90d"] = df["cites"].rolling(90, min_periods=1).mean()
    out["patents_chg_yoy"] = out["patents_count_90d"] - out["patents_count_90d"].shift(365)
    return out.reindex(dates).fillna(0.0)


def compute_inst_holdings_features(session, market, ticker, dates):
    cols = ["inst_filing_count_q", "inst_filing_count_q_chg"]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)
    rows = _safe_table_query(session, """
        SELECT period_end, COUNT(*) AS cnt FROM institutional_holdings
         WHERE market = :market AND ticker = :ticker
         GROUP BY period_end ORDER BY period_end
    """, {"market": market, "ticker": ticker})
    if not rows:
        return pd.DataFrame(0.0, index=dates, columns=cols)
    df = pd.DataFrame(rows, columns=["ts", "cnt"])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    out = pd.DataFrame(0.0, index=dates, columns=cols)
    for dt in dates:
        eligible = df[df.index <= dt]
        if len(eligible) >= 1:
            out.at[dt, "inst_filing_count_q"] = float(eligible.iloc[-1]["cnt"])
        if len(eligible) >= 2:
            out.at[dt, "inst_filing_count_q_chg"] = float(
                eligible.iloc[-1]["cnt"] - eligible.iloc[-2]["cnt"]
            )
    return out


def compute_gcam_features(session, market, ticker, dates):
    cols = ["gcam_fear_7d", "gcam_anger_7d", "gcam_econ_neg_7d", "gcam_polarity_30d"]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)
    start = pd.Timestamp(dates.min()).date() - timedelta(days=45)
    end = pd.Timestamp(dates.max()).date()
    rows = _safe_table_query(session, """
        SELECT article_published_ts::date AS ts,
               AVG(gcam_fear) AS fear, AVG(gcam_anger) AS anger,
               AVG(gcam_econ_neg) AS econ_neg, AVG(gcam_polarity) AS pol
          FROM gdelt_gcam
         WHERE market = :market AND ticker = :ticker
           AND article_published_ts >= :start AND article_published_ts <= :end
         GROUP BY article_published_ts::date
         ORDER BY 1
    """, {"market": market, "ticker": ticker,
           "start": pd.Timestamp(start), "end": pd.Timestamp(end) + pd.Timedelta(days=1)})
    if not rows:
        return pd.DataFrame(0.0, index=dates, columns=cols)
    df = pd.DataFrame(rows, columns=["ts", "fear", "anger", "econ_neg", "pol"])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    full = pd.date_range(df.index.min(), dates.max(), freq="D")
    df = df.reindex(full).fillna(0)
    out = pd.DataFrame(index=df.index)
    out["gcam_fear_7d"] = df["fear"].rolling(7, min_periods=1).mean()
    out["gcam_anger_7d"] = df["anger"].rolling(7, min_periods=1).mean()
    out["gcam_econ_neg_7d"] = df["econ_neg"].rolling(7, min_periods=1).mean()
    out["gcam_polarity_30d"] = df["pol"].rolling(30, min_periods=1).mean()
    return out.reindex(dates).fillna(0.0)


def compute_alt_data_features(session, market, ticker, dates) -> pd.DataFrame:
    """All alt-data features. Each sub-function returns zeros if its
    source table is empty (i.e. ingest script hasn't run yet)."""
    parts = [
        compute_short_interest_features(session, market, ticker, dates),
        compute_options_features(session, market, ticker, dates),
        compute_wiki_features(session, market, ticker, dates),
        compute_trends_features(session, market, ticker, dates),
        compute_reddit_features(session, market, ticker, dates),
        compute_patents_features(session, market, ticker, dates),
        compute_inst_holdings_features(session, market, ticker, dates),
        compute_gcam_features(session, market, ticker, dates),
    ]
    return pd.concat(parts, axis=1)
