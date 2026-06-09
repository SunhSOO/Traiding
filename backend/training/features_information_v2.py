"""Wave 2 — Comprehensive Information features.

  - News v2 (8):     velocity, spike_z, source_quality_weighted_sent,
                     headline_body_divergence, topic_ma, topic_legal,
                     topic_earnings, topic_product
  - Insider v2 (6):  cluster_buy_30d, ceo_cfo_cobuy_30d, openmarket_ratio,
                     insider_avg_cost_dist, exec_buy_weight, dir_buy_weight
  - SEC text (5):    lm_sent_10k, risk_factor_chg, fog_index, going_concern_count,
                     restatement_flag (latest 10-K from disclosures)
  - Disclosure v2 (4): 8k_item_categorical_recent, days_since_proxy,
                     days_since_s1, days_since_s4
  - GDELT GCAM (8):  amp1, amp2, polarity, persons_count, orgs_count,
                     themes_count, gcam_econ_negative, gcam_pol_negative
"""
from __future__ import annotations

from datetime import date as DateType, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session


# ──────────────────────────────────────────────────────────────────────
# News v2
# ──────────────────────────────────────────────────────────────────────


_HIGH_QUALITY_SOURCES = {
    "reuters", "bloomberg", "wsj", "ft", "nytimes", "cnbc",
    "marketwatch", "yonhap", "chosun", "joongang", "donga",
}


def compute_news_v2(session: Session, market: str, ticker: str,
                     dates: pd.DatetimeIndex) -> pd.DataFrame:
    cols = [
        "news_velocity_7d", "news_spike_z_30d", "news_source_q_sent_7d",
        "news_headline_body_div_7d",
        "news_topic_ma_7d", "news_topic_legal_30d",
        "news_topic_earnings_7d", "news_topic_product_30d",
    ]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)

    start = pd.Timestamp(dates.min()).date() - timedelta(days=60)
    end = pd.Timestamp(dates.max()).date()
    q = text("""
        SELECT na.published_ts AT TIME ZONE 'UTC' AS ts,
               na.source, na.title, na.summary
          FROM news_articles na
          JOIN news_ticker_mentions m
            ON m.article_id = na.id
           AND m.article_published_ts = na.published_ts
         WHERE m.market = :market AND m.ticker = :ticker
           AND na.published_ts >= :start AND na.published_ts < :end
    """)
    rows = session.execute(q, {
        "market": market, "ticker": ticker,
        "start": pd.Timestamp(start), "end": pd.Timestamp(end) + pd.Timedelta(days=1),
    }).all()
    if not rows:
        out = pd.DataFrame(0.0, index=dates, columns=cols)
        return out
    df = pd.DataFrame(rows, columns=["ts", "source", "title", "summary"])
    df["ts"] = pd.to_datetime(df["ts"]).dt.normalize()
    df["src_q"] = df["source"].fillna("").str.lower().apply(
        lambda s: 1.0 if any(h in s for h in _HIGH_QUALITY_SOURCES) else 0.3
    )

    # Simple keyword-based topic flags
    title_l = df["title"].fillna("").str.lower()
    summary_l = df["summary"].fillna("").str.lower()
    text_all = title_l + " " + summary_l
    df["t_ma"] = text_all.str.contains(
        r"merger|acquisition|takeover|buyout|m&a", regex=True
    ).astype(int)
    df["t_legal"] = text_all.str.contains(
        r"lawsuit|sec investigation|fraud|sued|probe|antitrust", regex=True
    ).astype(int)
    df["t_earnings"] = text_all.str.contains(
        r"earnings|revenue|eps|beat|miss|guidance|outlook", regex=True
    ).astype(int)
    df["t_product"] = text_all.str.contains(
        r"launch|unveil|release|new product|innovation|patent", regex=True
    ).astype(int)

    # Naive headline vs body sentiment divergence via polarity word count
    pos_words = r"surge|jump|gain|beat|profit|growth|boost|strong"
    neg_words = r"plunge|drop|fall|miss|loss|decline|cut|weak|fraud"
    h_pos = title_l.str.count(pos_words); h_neg = title_l.str.count(neg_words)
    b_pos = summary_l.str.count(pos_words); b_neg = summary_l.str.count(neg_words)
    df["head_sent"] = h_pos - h_neg
    df["body_sent"] = b_pos - b_neg
    df["sent_divergence"] = (df["head_sent"] - df["body_sent"]).abs()

    daily = df.groupby("ts").agg(
        count=("ts", "size"),
        src_q_sent=("src_q", "sum"),
        sent_div=("sent_divergence", "mean"),
        t_ma=("t_ma", "sum"),
        t_legal=("t_legal", "sum"),
        t_earnings=("t_earnings", "sum"),
        t_product=("t_product", "sum"),
    )
    full_idx = pd.date_range(daily.index.min(), dates.max(), freq="D")
    daily = daily.reindex(full_idx).fillna(0.0)

    out = pd.DataFrame(index=daily.index)
    out["news_velocity_7d"] = daily["count"].diff(7) / 7.0
    cnt_mean = daily["count"].rolling(30, min_periods=5).mean()
    cnt_std = daily["count"].rolling(30, min_periods=5).std().replace(0, np.nan)
    out["news_spike_z_30d"] = (daily["count"] - cnt_mean) / cnt_std
    out["news_source_q_sent_7d"] = daily["src_q_sent"].rolling(7, min_periods=1).sum()
    out["news_headline_body_div_7d"] = daily["sent_div"].rolling(7, min_periods=1).mean()
    out["news_topic_ma_7d"] = daily["t_ma"].rolling(7, min_periods=1).sum()
    out["news_topic_legal_30d"] = daily["t_legal"].rolling(30, min_periods=1).sum()
    out["news_topic_earnings_7d"] = daily["t_earnings"].rolling(7, min_periods=1).sum()
    out["news_topic_product_30d"] = daily["t_product"].rolling(30, min_periods=1).sum()

    return out.reindex(dates).fillna(0.0)


# ──────────────────────────────────────────────────────────────────────
# Insider v2 (cluster, exec weight, openmarket)
# ──────────────────────────────────────────────────────────────────────


def compute_insider_v2(session: Session, market: str, ticker: str,
                        dates: pd.DatetimeIndex) -> pd.DataFrame:
    cols = ["insider_cluster_buy_30d", "insider_ceo_cfo_cobuy_30d",
            "insider_openmarket_ratio", "insider_avg_cost_dist",
            "insider_exec_buy_weight_30d", "insider_dir_buy_weight_30d"]
    if len(dates) == 0:
        return pd.DataFrame(columns=cols)

    start = pd.Timestamp(dates.min()).date() - timedelta(days=60)
    end = pd.Timestamp(dates.max()).date()
    q = text("""
        SELECT trade_date, role, transaction_type, shares, price_per_share
          FROM insider_transactions
         WHERE market = :market AND ticker = :ticker
           AND trade_date >= :start AND trade_date <= :end
    """)
    try:
        with session.begin_nested():
            rows = session.execute(q, {
                "market": market, "ticker": ticker, "start": start, "end": end,
            }).all()
    except Exception:
        rows = []
    if not rows:
        return pd.DataFrame(0.0, index=dates, columns=cols)
    df = pd.DataFrame(rows, columns=["trade_date", "role", "txn", "shares", "price"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["is_buy"] = df["txn"].str.upper().str.startswith("P") | df["txn"].str.contains("BUY", case=False)
    df["role_u"] = df["role"].fillna("").str.upper()
    df["is_exec"] = df["role_u"].str.contains("CEO|CFO|COO|PRES")
    df["is_dir"] = df["role_u"].str.contains("DIRECTOR")
    df["is_openmarket"] = df["txn"].str.contains("P", na=False) & ~df["txn"].str.contains("M", na=False)

    out = pd.DataFrame(0.0, index=dates, columns=cols)
    for dt in dates:
        d_end = dt.date() if hasattr(dt, "date") else dt
        d_start = d_end - timedelta(days=30)
        win = df[(df["trade_date"] >= pd.Timestamp(d_start)) &
                  (df["trade_date"] <= pd.Timestamp(d_end))]
        if win.empty:
            continue
        buyers = win[win["is_buy"]]["role"].dropna().unique()
        out.at[dt, "insider_cluster_buy_30d"] = float(len(buyers))
        out.at[dt, "insider_ceo_cfo_cobuy_30d"] = float(
            win[win["is_buy"] & win["is_exec"]]["role"].nunique() >= 2
        )
        n_total = len(win)
        n_openmarket = win["is_openmarket"].sum()
        out.at[dt, "insider_openmarket_ratio"] = n_openmarket / n_total if n_total else 0
        out.at[dt, "insider_exec_buy_weight_30d"] = float(
            (win[win["is_buy"] & win["is_exec"]]["shares"] *
             win[win["is_buy"] & win["is_exec"]]["price"]).sum()
        )
        out.at[dt, "insider_dir_buy_weight_30d"] = float(
            (win[win["is_buy"] & win["is_dir"]]["shares"] *
             win[win["is_buy"] & win["is_dir"]]["price"]).sum()
        )

    return out


# ──────────────────────────────────────────────────────────────────────
# SEC 10-K Loughran-McDonald (deferred to features_text.py until corpus exists)
# ──────────────────────────────────────────────────────────────────────


def compute_sec_text_v2(session: Session, market: str, ticker: str,
                         dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Stub — populated by scripts/edgar_10k_lm_sentiment.py.

    Reads from `disclosure_text_features` table (created when 10-K text
    is processed). Returns NaN columns when table absent.
    """
    cols = ["lm_sent_10k", "risk_factor_chg_pct", "fog_index_10k",
            "going_concern_count_10k", "restatement_flag"]
    out = pd.DataFrame(np.nan, index=dates, columns=cols)
    try:
        q = text("""
            SELECT period_end, lm_sentiment, risk_factor_word_change_pct,
                   fog_index, going_concern_count, restatement_flag
              FROM disclosure_text_features
             WHERE market = :market AND ticker = :ticker
        """)
        with session.begin_nested():
            rows = session.execute(q, {"market": market, "ticker": ticker}).all()
    except Exception:
        return out
    if not rows:
        return out
    df = pd.DataFrame(rows, columns=["period_end", "lm_sent", "rf_chg",
                                       "fog", "gc_count", "restate"])
    df["period_end"] = pd.to_datetime(df["period_end"])
    df = df.sort_values("period_end")
    for dt in dates:
        eligible = df[df["period_end"] <= dt]
        if eligible.empty:
            continue
        last = eligible.iloc[-1]
        out.at[dt, "lm_sent_10k"] = float(last["lm_sent"]) if last["lm_sent"] is not None else np.nan
        out.at[dt, "risk_factor_chg_pct"] = float(last["rf_chg"]) if last["rf_chg"] is not None else np.nan
        out.at[dt, "fog_index_10k"] = float(last["fog"]) if last["fog"] is not None else np.nan
        out.at[dt, "going_concern_count_10k"] = float(last["gc_count"] or 0)
        out.at[dt, "restatement_flag"] = float(last["restate"] or 0)
    return out


# ──────────────────────────────────────────────────────────────────────
# Top-level
# ──────────────────────────────────────────────────────────────────────


def compute_information_v2(session: Session, market: str, ticker: str,
                            dates: pd.DatetimeIndex) -> pd.DataFrame:
    parts = []
    parts.append(compute_news_v2(session, market, ticker, dates))
    parts.append(compute_insider_v2(session, market, ticker, dates))
    parts.append(compute_sec_text_v2(session, market, ticker, dates))
    return pd.concat(parts, axis=1)
