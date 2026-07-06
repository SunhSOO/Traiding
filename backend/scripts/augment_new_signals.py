"""Augment existing period caches with the NEW IC-validated signal columns —
FX/overnight-proxy betas, Amihud illiquidity, FINRA short-interest, KR bond
curve, PEAD recency — using the SAME pipeline functions live inference uses, so
training cache and live features match WITHOUT a full per-ticker rebuild.

(fwd-estimate revision cols are NOT augmented: no 2018-2024 history — they accrue
forward from the weekly snapshot job.)

Usage: uv run python scripts/augment_new_signals.py --market KR
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from sqlalchemy import text

from core.db import session_scope
from training.features import (
    compute_beta_features, compute_liquidity_features,
    compute_short_interest_features, load_macro_features,
)

PERIOD = "2018-01-01_2024-01-01"
BETA = ["beta_usdkrw_63d", "beta_usdkrw_126d", "beta_usdcny_63d",
        "beta_soxx_63d", "beta_ewy_63d"]
LIQ = ["amihud_illiq_21d", "amihud_illiq_63d", "amihud_illiq_z_60d"]
SI = ["si_days_to_cover", "si_change_pct", "si_shares_z_12p"]        # US only
PEAD = ["days_since_q_filing"]
KRMACRO = ["kr_10y", "kr_10y_21d_chg", "kr_term_spread_3_10",
           "kospi_realized_vol_21d", "kospi_rv_pctile_252d"]         # KR only


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    a = ap.parse_args()
    cache = Path(f"var/_bt_period_{a.market}_{PERIOD}.parquet")
    df = pd.read_parquet(cache)
    df["date"] = pd.to_datetime(df["date"])
    dates = pd.DatetimeIndex(np.sort(df["date"].unique()))
    lo, hi = dates.min().date(), dates.max().date()

    new_cols = BETA + LIQ + PEAD + (SI if a.market == "US" else []) \
        + (KRMACRO if a.market == "KR" else [])
    df = df.drop(columns=[c for c in new_cols if c in df.columns], errors="ignore")
    n0 = len(df)

    with session_scope() as s:
        rows = s.execute(text(
            "SELECT trade_date,ticker,close,volume FROM daily_prices "
            "WHERE market=:m AND trade_date BETWEEN :a AND :b"),
            {"m": a.market, "a": lo, "b": hi}).all()
        ff = s.execute(text(
            "SELECT ticker, period_end FROM financial_facts "
            "WHERE market=:m AND period_kind='Q'"), {"m": a.market}).all()
        krmac = load_macro_features(s, dates) if a.market == "KR" else None

        px = pd.DataFrame(rows, columns=["date", "ticker", "close", "volume"])
        px["date"] = pd.to_datetime(px["date"])
        ffdf = pd.DataFrame(ff, columns=["ticker", "pe"])
        ffdf["avail"] = pd.to_datetime(ffdf["pe"]) + pd.Timedelta(days=45)
        avail_by = {tk: np.sort(g["avail"].values) for tk, g in ffdf.groupby("ticker")}

        out = []
        for tkr, g in px.groupby("ticker"):
            bars = g.set_index("date").sort_index()[["close", "volume"]].astype(float)
            if len(bars) < 130:
                continue
            di = bars.index
            close = bars["close"]
            bt = compute_beta_features(s, di, close)
            lq = compute_liquidity_features(bars)
            f = pd.DataFrame(index=di)
            for c in BETA:
                if c in bt.columns:
                    f[c] = bt[c]
            for c in LIQ:
                if c in lq.columns:
                    f[c] = lq[c]
            if a.market == "US":
                si = compute_short_interest_features(s, "US", tkr, di)
                for c in SI:
                    if c in si.columns:
                        f[c] = si[c]
            av = avail_by.get(tkr)
            if av is not None and len(av):
                pos = np.searchsorted(av, di.values, side="right") - 1
                dsf = np.full(len(di), np.nan)
                m = pos >= 0
                dsf[m] = (di.values[m] - av[pos[m]]) / np.timedelta64(1, "D")
                f["days_since_q_filing"] = dsf
            f["ticker"] = tkr
            f["date"] = f.index
            out.append(f.reset_index(drop=True))

    newf = pd.concat(out, ignore_index=True)
    df = df.merge(newf, on=["ticker", "date"], how="left")
    assert len(df) == n0, "per-ticker merge changed row count"
    if a.market == "KR" and krmac is not None:
        km = krmac[KRMACRO].copy()
        km["date"] = km.index
        df = df.merge(km, on="date", how="left")
        assert len(df) == n0, "macro merge changed row count"

    cov = df[new_cols].notna().mean().round(3).to_dict()
    df.to_parquet(cache)
    print(f"[aug] {a.market} rows={len(df):,} cols={df.shape[1]}", flush=True)
    print(f"[aug] coverage: {cov}", flush=True)


if __name__ == "__main__":
    main()
