"""Augment the US cache with short-volume features (genuinely orthogonal data).

short_volume_daily (FINRA, 17.9M rows, US) is in the DB but NOT featurized. High/
rising short ratio = bearish pressure / squeeze potential — orthogonal to price &
fundamentals. Features per (ticker,date), lagged 1 day for point-in-time safety
(short data for day d is published d+1):
  sv_ratio    : short_volume/total_volume (level)
  sv_chg5/21  : 5d/21d change in short ratio (rising short pressure)
  sv_z63      : 63d z-score of short ratio (extreme positioning)

Usage: uv run python scripts/augment_shortvol.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from sqlalchemy import text
from core.db import session_scope

PERIOD = "2018-01-01_2024-01-01"
SV = ["sv_ratio", "sv_chg5", "sv_chg21", "sv_z63"]


def main():
    cache = Path(f"var/_bt_period_US_{PERIOD}.parquet")
    df = pd.read_parquet(cache); df["date"] = pd.to_datetime(df["date"])
    if any(c in df.columns for c in SV):
        df = df.drop(columns=[c for c in SV if c in df.columns])
    tickers = df["ticker"].unique().tolist()
    with session_scope() as s:
        rows = s.execute(text(
            "SELECT ticker, ts, short_volume, total_volume FROM short_volume_daily "
            "WHERE market='US' AND ts BETWEEN '2017-09-01' AND '2024-01-05' "
            "AND total_volume > 0 AND ticker = ANY(:tk)"), {"tk": tickers}).all()
    sv = pd.DataFrame(rows, columns=["ticker", "date", "short_volume", "total_volume"])
    sv["date"] = pd.to_datetime(sv["date"])
    sv = sv.sort_values(["ticker", "date"])
    sv["sv_ratio"] = sv["short_volume"] / sv["total_volume"].replace(0, np.nan)
    g = sv.groupby("ticker")["sv_ratio"]
    sv["sv_chg5"] = g.transform(lambda x: x - x.shift(5))
    sv["sv_chg21"] = g.transform(lambda x: x - x.shift(21))
    sv["sv_z63"] = g.transform(lambda x: (x - x.rolling(63, min_periods=20).mean())
                               / (x.rolling(63, min_periods=20).std() + 1e-9))
    # PIT: shift each ticker's features forward 1 row (day d uses <= d-1 data)
    for c in SV:
        sv[c] = sv.groupby("ticker")[c].shift(1)
    n0 = len(df)
    df = df.merge(sv[["ticker", "date"] + SV], on=["ticker", "date"], how="left")
    assert len(df) == n0, "merge changed rows"
    cov = df[SV].notna().mean().round(3).to_dict()
    df.to_parquet(cache)
    print(f"[sv] US rows={len(df):,} cols={df.shape[1]} short-vol coverage={cov} -> {cache}", flush=True)


if __name__ == "__main__":
    main()
