"""Cross-market pooled training — does training on US+KR together help each market?

KR is small/noisy (~350 names) which broke lambdarank. Pooling gives the model
US data too (shared cross-sectional structure: momentum/value/low-vol behave
similarly across markets). We per-(market,date) z-score features so a US and a
KR stock are comparable, concatenate, train ONE model, evaluate EACH market
separately vs its own-market baseline (mn_swabs: KR IC 0.0109 / US 0.0356).

Usage: uv run python scripts/pool_lab.py --seeds 42,1,7
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.stats import spearmanr
from sqlalchemy import text
from core.db import session_scope
from scripts.train_lgbm import ALL_FEATURE_COLS

PERIOD = "2018-01-01_2024-01-01"
BASE = dict(n_estimators=300, num_leaves=31, learning_rate=0.04, min_child_samples=100,
            subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, verbose=-1, n_jobs=-1)


def _px(market, lo, hi):
    with session_scope() as s:
        rows = s.execute(text("SELECT trade_date,ticker,close FROM daily_prices "
                              "WHERE market=:m AND trade_date BETWEEN :a AND :b"),
                         {"m": market, "a": lo, "b": hi}).all()
    p = pd.DataFrame(rows, columns=["date", "ticker", "close"]); p["date"] = pd.to_datetime(p["date"])
    return p.pivot_table(index="date", columns="ticker", values="close", aggfunc="first")


def _load(market, feats_common):
    df = pd.read_parquet(Path(f"var/_bt_period_{market}_{PERIOD}.parquet"))
    df["date"] = pd.to_datetime(df["date"]); df["market"] = market
    df["mn"] = df["ret_fwd_21d"] - df.groupby("date")["ret_fwd_21d"].transform("mean")
    g = df.groupby("date")
    z = (df[feats_common] - g[feats_common].transform("mean")) / (g[feats_common].transform("std") + 1e-9)
    z.columns = [c + "_z" for c in feats_common]
    return pd.concat([df[["date", "ticker", "market", "mn", "ret_fwd_21d"]], z], axis=1)


def run(seed, step=42, topk=50):
    kr0 = pd.read_parquet(Path(f"var/_bt_period_KR_{PERIOD}.parquet"))
    us0 = pd.read_parquet(Path(f"var/_bt_period_US_{PERIOD}.parquet"))
    feats_common = [c for c in ALL_FEATURE_COLS if c in kr0.columns and c in us0.columns]
    del kr0, us0
    kr = _load("KR", feats_common); us = _load("US", feats_common)
    zf = [c + "_z" for c in feats_common]
    pool = pd.concat([kr, us], ignore_index=True)
    px = {"KR": _px("KR", pool.date.min().date(), pool.date.max().date()),
          "US": _px("US", pool.date.min().date(), pool.date.max().date())}
    cost = {"KR": 0.003, "US": 0.001}

    dates = np.sort(pool["date"].unique())
    rebal = list(range(63, len(dates) - 1, step))
    B = dict(BASE); B["random_state"] = seed
    # feature selection once on pooled initial window
    cut0 = dates[max(0, 63 - 21)]
    tr0 = pool[pool["date"] <= cut0].dropna(subset=["mn"])
    sel = lgb.LGBMRegressor(**B).fit(tr0[zf].astype(float), tr0["mn"].astype(float),
                                     sample_weight=np.abs(tr0["mn"].values) + 1e-6)
    top = pd.Series(sel.feature_importances_, index=zf).sort_values(ascending=False).head(topk).index.tolist()

    out = {m: {"cash": 1.0, "bench": 1.0, "ics": []} for m in ("KR", "US")}
    for j, i in enumerate(rebal):
        R = dates[i]; E = dates[rebal[j + 1]] if j + 1 < len(rebal) else dates[-1]
        cut = dates[max(0, i - 21)]
        tr = pool[pool["date"] <= cut].dropna(subset=["mn"])
        if len(tr) < 1000:
            continue
        m = lgb.LGBMRegressor(**B).fit(tr[top].astype(float), tr["mn"].astype(float),
                                       sample_weight=np.abs(tr["mn"].values) + 1e-6)
        for mkt in ("KR", "US"):
            atR = pool[(pool["date"] == R) & (pool["market"] == mkt)].copy()
            if atR.empty:
                continue
            atR["score"] = m.predict(atR[top].astype(float))
            atR["pct"] = atR["score"].rank(pct=True)
            P = px[mkt]
            if R not in P.index or E not in P.index:
                continue

            def ret(tks):
                r = [float(P.at[E, t]) / float(P.at[R, t]) - 1 for t in tks
                     if t in P.columns and pd.notna(P.at[R, t]) and pd.notna(P.at[E, t]) and P.at[R, t] > 0]
                return float(np.mean(r)) if r else 0.0
            longs = atR[atR["pct"] >= 0.9]["ticker"].tolist()
            out[mkt]["cash"] *= (1 + ret(longs) - cost[mkt])
            out[mkt]["bench"] *= (1 + ret(list(P.columns)))
            fr = (P.loc[E] / P.loc[R] - 1)
            frv = pd.to_numeric(atR["ticker"].map(fr), errors="coerce").to_numpy(float)
            sv = pd.to_numeric(atR["score"], errors="coerce").to_numpy(float)
            ok = np.isfinite(frv) & np.isfinite(sv)
            if ok.sum() > 10:
                out[mkt]["ics"].append(spearmanr(sv[ok], frv[ok]).correlation)
    ny = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[63])).days / 365.25
    res = {}
    for mkt in ("KR", "US"):
        o = out[mkt]; ic = np.array([x for x in o["ics"] if np.isfinite(x)])
        res[mkt] = ((o["cash"] - o["bench"]) / ny * 100, float(ic.mean()) if len(ic) else float("nan"))
    return res


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seeds", default="42,1,7"); ap.add_argument("--step", type=int, default=42)
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",")]
    print(f"[pool] US+KR pooled training, seeds={seeds}", flush=True)
    acc = {"KR": {"a": [], "ic": []}, "US": {"a": [], "ic": []}}
    for sd in seeds:
        r = run(sd, step=a.step)
        for m in ("KR", "US"):
            acc[m]["a"].append(r[m][0]); acc[m]["ic"].append(r[m][1])
        print(f"  seed{sd}: KR alpha {r['KR'][0]:+.1f}% IC {r['KR'][1]:+.4f} | US alpha {r['US'][0]:+.1f}% IC {r['US'][1]:+.4f}", flush=True)
    print("\n=== POOLED vs own-market baseline (mn_swabs: KR IC 0.0109 / US 0.0356) ===")
    for m in ("KR", "US"):
        print(f"{m}: alpha {np.mean(acc[m]['a']):+.1f}±{np.std(acc[m]['a']):.1f}%/yr  IC {np.nanmean(acc[m]['ic']):+.4f}", flush=True)


if __name__ == "__main__":
    main()
