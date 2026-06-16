"""Alpha lab — fast A/B harness for alpha-improvement levers.

Reuses the prebuilt 2018-2024 multi-regime matrices (var/_bt_period_{m}_2018-01-01_2024-01-01.parquet)
so every experiment is just a retrain+walk-forward (~minutes), no rebuild.

Each run_experiment(...) does an expanding-window, no-look-ahead walk-forward
(21d embargo, retrain each step) and returns the HONEST metrics we trust:
  alpha (strategy - equal-weight benchmark, total + per-year), MDD, IC,
  and the VIX-regime breakdown.

Config levers:
  label        : 'rank' (cross-sectional rank of fwd ret) | 'ret' (raw) |
                 'mn'   (market-neutral residual = ret - date-mean; targets pure alpha)
  topk         : # features by importance
  regime_cond  : per-VIX-bucket models (momentum<->mean-reversion switch)
  portfolio    : 'long' (top decile) | 'ls' (long top - short bottom, market-neutral)
  vix_gate     : cash when vix percentile >= this (bear defense) | None
  model        : 'lgbm' (more to come: xgb/cat/ensemble)

Usage:
    uv run python scripts/alpha_lab.py --market KR --configs baseline,regime,mn,ls,gate
"""
from __future__ import annotations

import argparse
import sys
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
            subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, verbose=-1, n_jobs=-1,
            random_state=42)


def _base(seed):
    b = dict(BASE); b["random_state"] = seed; return b


def _close_panel(market, lo, hi):
    with session_scope() as s:
        rows = s.execute(text(
            "SELECT trade_date,ticker,close FROM daily_prices "
            "WHERE market=:m AND trade_date BETWEEN :a AND :b"), {"m": market, "a": lo, "b": hi}).all()
    p = pd.DataFrame(rows, columns=["date", "ticker", "close"]); p["date"] = pd.to_datetime(p["date"])
    return p.pivot_table(index="date", columns="ticker", values="close", aggfunc="first")


def _vix_bucket(v):
    if not np.isfinite(v):
        return 1
    return 0 if v < 0.4 else (2 if v >= 0.7 else 1)


def run_experiment(df, market, *, label="rank", topk=50, regime_cond=False,
                   portfolio="long", vix_gate=None, decile=0.1, step=21, cost=None,
                   reselect=False, seed=42, model="lgbm"):
    cost = cost if cost is not None else (0.003 if market == "KR" else 0.001)
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    dates = np.sort(df["date"].unique())
    px = _close_panel(market, pd.Timestamp(dates[0]).date(), pd.Timestamp(dates[-1]).date())

    # target column
    if label == "rank":
        tgt = "rank_fwd_21d"
    elif label == "ret":
        tgt = "ret_fwd_21d"
    elif label == "mn":   # market-neutral residual fwd return
        df = df.copy()
        df["mn_fwd_21d"] = df["ret_fwd_21d"] - df.groupby("date")["ret_fwd_21d"].transform("mean")
        tgt = "mn_fwd_21d"
    elif label == "sn":   # sector-neutral residual (subtract date-sector mean)
        df = df.copy()
        with session_scope() as s:
            secmap = {t: sec for t, sec in s.execute(text(
                "SELECT ticker, COALESCE(sector,'NA') FROM securities WHERE market=:m"),
                {"m": market}).all()}
        df["_sec"] = df["ticker"].map(secmap).fillna("NA")
        df["sn_fwd_21d"] = df["ret_fwd_21d"] - df.groupby(["date", "_sec"])["ret_fwd_21d"].transform("mean")
        tgt = "sn_fwd_21d"
    else:
        raise ValueError(label)

    start_idx = 63
    rebal = list(range(start_idx, len(dates) - 1, step))
    # Feature selection ONCE on the initial training window (past data only;
    # importance is stable enough that per-rebalance reselection isn't worth
    # the 5x cost). 'top' is reused for every rebalance.
    init_cut = dates[max(0, start_idx - 21)]
    tr0 = df[df["date"] <= init_cut].dropna(subset=[tgt])
    sel = lgb.LGBMRegressor(**_base(seed)).fit(tr0[feats].astype(float), tr0[tgt].astype(float))
    top = pd.Series(sel.feature_importances_, index=feats).sort_values(ascending=False).head(topk).index.tolist()
    if regime_cond and "vix_pctile_252d" in df.columns:
        df = df.assign(_vb=df["vix_pctile_252d"].apply(_vix_bucket))

    cash = bench = 1.0
    log = []
    for j, i in enumerate(rebal):
        R = dates[i]; E = dates[rebal[j+1]] if j+1 < len(rebal) else dates[-1]
        cut = dates[max(0, i-21)]
        tr = df[df["date"] <= cut].dropna(subset=[tgt])
        atR = df[df["date"] == R].copy()
        if len(tr) < 500 or atR.empty:
            continue
        vp = float(atR["vix_pctile_252d"].iloc[0]) if "vix_pctile_252d" in atR.columns else np.nan
        if regime_cond and "_vb" in df.columns:
            tr_b = tr[tr["_vb"] == _vix_bucket(vp)]
            tr_use = tr_b if len(tr_b) > 500 else tr
        else:
            tr_use = tr
        top_r = top
        if reselect:  # re-select features from this window's past data
            selr = lgb.LGBMRegressor(**_base(seed)).fit(tr[feats].astype(float), tr[tgt].astype(float))
            top_r = pd.Series(selr.feature_importances_, index=feats).sort_values(ascending=False).head(topk).index.tolist()
        Xtr, ytr, Xte = tr_use[top_r].astype(float), tr_use[tgt].astype(float), atR[top_r].astype(float)
        m = lgb.LGBMRegressor(**_base(seed)).fit(Xtr, ytr)
        if model == "ens":
            from sklearn.ensemble import HistGradientBoostingRegressor as HGB
            h = HGB(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                    l2_regularization=1.0, random_state=seed).fit(Xtr, ytr)
            s1 = pd.Series(m.predict(Xte)).rank(pct=True).values
            s2 = pd.Series(h.predict(Xte)).rank(pct=True).values
            atR["score"] = (s1 + s2) / 2.0
        else:
            atR["score"] = m.predict(Xte)
        atR["pct"] = atR["score"].rank(pct=True)

        gated = vix_gate is not None and np.isfinite(vp) and vp >= vix_gate

        def ret(tks):
            r = [px.at[E, t]/px.at[R, t]-1 for t in tks
                 if t in px.columns and R in px.index and E in px.index
                 and pd.notna(px.at[R, t]) and pd.notna(px.at[E, t]) and px.at[R, t] > 0]
            return float(np.mean(r)) if r else 0.0

        longs = atR[atR["pct"] >= 1-decile]["ticker"].tolist()
        if gated:
            port = 0.0
        elif portfolio == "ls":
            shorts = atR[atR["pct"] <= decile]["ticker"].tolist()
            port = (ret(longs) - ret(shorts)) - 2*cost
        else:
            port = ret(longs) - cost
        allr = ret(list(px.columns))
        cash *= (1+port); bench *= (1+allr)
        log.append((port, allr, cash, vp))

    L = pd.DataFrame(log, columns=["port", "bench", "cash", "vix"])
    if L.empty:
        return {}
    n_years = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[start_idx])).days / 365.25
    tot = cash - 1; btot = bench - 1
    eq = L["cash"].values
    mdd = float((eq/np.maximum.accumulate(eq) - 1).min())
    vb = {}
    for nm, msk in [("lo", L.vix < 0.4), ("mid", (L.vix >= 0.4) & (L.vix < 0.7)), ("hi", L.vix >= 0.7)]:
        s = L[msk]
        vb[nm] = round((s["port"]-s["bench"]).mean()*100, 2) if len(s) else None
    return {"ret": round(tot*100, 1), "bench": round(btot*100, 1),
            "alpha_total": round((tot-btot)*100, 1),
            "alpha_yr": round((tot-btot)/n_years*100, 2),
            "mdd": round(mdd*100, 1), "win": round((L.port > L.bench).mean()*100),
            "vix_alpha": vb, "n": len(L)}


CONFIGS = {
    "baseline":   dict(label="rank", regime_cond=False, portfolio="long"),
    "mn":         dict(label="mn",   regime_cond=False, portfolio="long"),
    "regime":     dict(label="rank", regime_cond=True,  portfolio="long"),
    "regime_mn":  dict(label="mn",   regime_cond=True,  portfolio="long"),
    "ls":         dict(label="rank", regime_cond=False, portfolio="ls"),
    "gate":       dict(label="rank", regime_cond=False, portfolio="long", vix_gate=0.7),
    "regime_gate":dict(label="rank", regime_cond=True,  portfolio="long", vix_gate=0.7),
    # reselect (per-rebalance feature selection) variants — reconcile vs backtest_capital
    "baseline_rs":dict(label="rank", regime_cond=False, portfolio="long", reselect=True),
    "mn_rs":      dict(label="mn",   regime_cond=False, portfolio="long", reselect=True),
    "regime_rs":  dict(label="rank", regime_cond=True,  portfolio="long", reselect=True),
    "regime_mn_rs":dict(label="mn",  regime_cond=True,  portfolio="long", reselect=True),
    # mn-label lever stack (build on the validated winner)
    "mn_top30":   dict(label="mn", regime_cond=False, portfolio="long", reselect=True, topk=30),
    "mn_top100":  dict(label="mn", regime_cond=False, portfolio="long", reselect=True, topk=100),
    "mn_ls":      dict(label="mn", regime_cond=False, portfolio="ls",   reselect=True),
    "sn_rs":      dict(label="sn", regime_cond=False, portfolio="long", reselect=True),
    "mn_ens":     dict(label="mn", regime_cond=False, portfolio="long", reselect=True, model="ens"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR")
    ap.add_argument("--configs", default="baseline,mn,regime,ls,gate")
    ap.add_argument("--seeds", default="42", help="comma-separated seeds; >1 => mean±std")
    ap.add_argument("--step", type=int, default=21)
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    cache = Path(f"var/_bt_period_{args.market}_{PERIOD}.parquet")
    df = pd.read_parquet(cache); df["date"] = pd.to_datetime(df["date"])
    print(f"[lab] {args.market} rows={len(df):,} seeds={seeds} step={args.step}", flush=True)
    if len(seeds) > 1:
        print(f"{'config':<13} {'alpha/yr mean±std':>22} {'min..max':>14} {'MDD~':>7}")
    else:
        print(f"{'config':<13} {'alpha/yr':>9} {'alpha_tot':>9} {'MDD':>7} {'win%':>5}  vix(lo/mid/hi)")
    for name in args.configs.split(","):
        cfg = dict(CONFIGS[name.strip()]); cfg["step"] = args.step
        ays, mdds, last = [], [], None
        for sd in seeds:
            m = run_experiment(df, args.market, seed=sd, **cfg)
            if m:
                ays.append(m["alpha_yr"]); mdds.append(m["mdd"]); last = m
        if not ays:
            continue
        if len(seeds) > 1:
            a = np.array(ays)
            print(f"{name:<13} {a.mean():>11.2f} ± {a.std():>5.2f}%/yr {a.min():>6.1f}..{a.max():<5.1f} "
                  f"{np.mean(mdds):>6.1f}%", flush=True)
        else:
            print(f"{name:<13} {last['alpha_yr']:>8.2f}% {last['alpha_total']:>8.1f}% {last['mdd']:>6.1f}% "
                  f"{last['win']:>4}%  {last['vix_alpha']}", flush=True)


if __name__ == "__main__":
    main()
