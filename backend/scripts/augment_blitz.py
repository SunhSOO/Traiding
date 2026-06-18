"""Augment an existing period cache with the Blitz residual-momentum columns,
computed by the SAME pipeline function (compute_stat_features) and the SAME
market proxy (SPY via cross-asset panel) that live inference uses — so the
training cache and live features are identical for these columns, WITHOUT a
15h full per-ticker rebuild (we skip the slow fundamental/news/disclosure work).

Usage: uv run python scripts/augment_blitz.py --market US
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from sqlalchemy import text
from core.db import session_scope
from training.features_advanced import compute_stat_features

PERIOD = "2018-01-01_2024-01-01"
BLITZ = ["resid_mom_blitz_12m", "resid_mom_blitz_6m"]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--market", required=True); a = ap.parse_args()
    cache = Path(f"var/_bt_period_{a.market}_{PERIOD}.parquet")
    df = pd.read_parquet(cache); df["date"] = pd.to_datetime(df["date"])
    if any(c in df.columns for c in BLITZ):
        print(f"[aug] {a.market} already has blitz cols; dropping to recompute")
        df = df.drop(columns=[c for c in BLITZ if c in df.columns])
    dates = pd.DatetimeIndex(np.sort(df["date"].unique()))
    lo, hi = dates.min().date(), dates.max().date()

    # prices (close) per ticker, and the SAME market proxy the pipeline uses
    with session_scope() as s:
        rows = s.execute(text(
            "SELECT trade_date,ticker,close FROM daily_prices "
            "WHERE market=:m AND trade_date BETWEEN :a AND :b"),
            {"m": a.market, "a": lo, "b": hi}).all()
        from training.features import _load_cross_asset_panel
        ca = _load_cross_asset_panel(s, dates)
    mkt_close = ca["SPY"] if "SPY" in ca.columns else None
    if mkt_close is None:
        raise SystemExit("[aug] no SPY in cross-asset panel; cannot match pipeline proxy")
    px = pd.DataFrame(rows, columns=["date", "ticker", "close"]); px["date"] = pd.to_datetime(px["date"])

    out = []
    for tkr, g in px.groupby("ticker"):
        bars = g.set_index("date").sort_index()[["close"]].astype(float)
        if len(bars) < 130:
            continue
        stat = compute_stat_features(bars, mkt_close)   # identical to pipeline
        bl = stat[BLITZ].copy(); bl["ticker"] = tkr; bl["date"] = bl.index
        out.append(bl.reset_index(drop=True))
    blitz = pd.concat(out, ignore_index=True)
    n0 = len(df)
    df = df.merge(blitz, on=["ticker", "date"], how="left")
    assert len(df) == n0, "merge changed row count"
    cov = df[BLITZ].notna().mean().round(3).to_dict()
    df.to_parquet(cache)
    print(f"[aug] {a.market} rows={len(df):,} cols={df.shape[1]} blitz coverage={cov} -> {cache}", flush=True)


if __name__ == "__main__":
    main()
