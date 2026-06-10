"""Persist HMM regime as a per-date time series into market_regime.

`regime/hmm_classifier.train_and_predict` fits a 5-state Gaussian HMM on the
full macro history (VIX/DXY/SP500/yield-curve) and returns a per-date label +
posterior — but it never writes anything. As a result `market_regime` held only
6 stale NEUTRAL rows, so the regime features (regime_risk_on/off/conf) were
dead (always 0).

This backfill runs the HMM once and upserts one market_regime row per trading
date for BOTH markets (risk regime is global — driven by US macro). The 5-state
label is stored as-is; `load_regime_features` maps it to the on/off/confidence
features (and the new 5-state one-hots).

Usage:
    uv run python scripts/backfill_regime_history.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import psycopg

from core.config import get_settings
from core.db import session_scope
from regime.hmm_classifier import train_and_predict


def _dsn() -> str:
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")


def main() -> None:
    print("[regime] fitting HMM on full macro history ...", flush=True)
    with session_scope() as s:
        df = train_and_predict(s)
    if df is None or df.empty:
        print("[regime] HMM produced nothing (hmmlearn missing or insufficient history)")
        return
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    print(f"[regime] HMM dates={len(df)} {df.index.min().date()}..{df.index.max().date()}",
          flush=True)
    print("[regime] label distribution:")
    for lbl, n in df["hmm_label"].value_counts().items():
        print(f"    {lbl:12} {n:>5} ({100*n/len(df):.0f}%)")

    rows = []
    post_cols = [c for c in df.columns if c.startswith("hmm_post_")]
    for ts, r in df.iterrows():
        raw = {c.replace("hmm_post_", ""): round(float(r[c]), 4) for c in post_cols}
        for mk in ("KR", "US"):
            rows.append((mk, ts.date(), r["hmm_label"], float(r["hmm_confidence"]),
                         psycopg.types.json.Json(raw), psycopg.types.json.Json(raw)))

    UP = """
        INSERT INTO market_regime (market, ts, label, confidence, votes, raw_inputs)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT ON CONSTRAINT pk_market_regime DO UPDATE SET
            label = EXCLUDED.label,
            confidence = EXCLUDED.confidence,
            votes = EXCLUDED.votes,
            raw_inputs = EXCLUDED.raw_inputs
    """
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.executemany(UP, rows)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT market, count(*), min(ts), max(ts) "
                        "FROM market_regime GROUP BY market")
            print("[regime] market_regime now:", cur.fetchall())
    print(f"[regime] DONE — upserted {len(rows):,} rows ({len(df):,} dates x 2 markets).",
          flush=True)


if __name__ == "__main__":
    main()
