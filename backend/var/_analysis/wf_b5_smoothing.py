"""B5 — net-of-cost rank smoothing: the campaign never once optimized NET excess
(every adoption judged GROSS). Selection churns ~90% per rebalance; EWMA-smoothing
each name's cross-sectional model rank across rebalances cuts turnover, so a NET
win is near-mechanical even at flat gross — and smoothing may de-noise selection
for a small gross lift too.

Per market (US 6yr @10bps, KR 6yr @30bps), walk-forward:
  raw     : select top decile by this fold's model rank
  ewma(a) : select top decile by EWMA(a) of the rank across folds
For each: gross top-decile excess, mean turnover (1-Jaccard vs prev basket),
NET excess = gross - turnover*(2*cost), net Sharpe, and win-vs-raw on NET.
Adopt smoothing only if NET rises with no material gross-excess loss.

Usage: uv run python var/_analysis/wf_b5_smoothing.py
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from scripts.train_lgbm import ALL_FEATURE_COLS

RET, TGT = "ret_fwd_21d", "rank_fwd_21d"
LGB = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
STEP, TRAIN_MIN, TRAIL, EMB, DEC = 63, 504, 756, 21, 0.9
EWMAS = [1.0, 0.5, 0.3]   # 1.0 == raw (no smoothing)
COST = {"US": 0.0010, "KR": 0.0030}   # one-way


def zscore(df, feats):
    g = df.groupby("date")
    return (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)


for MARKET in ("US", "KR"):
    df = pd.read_parquet(f"var/_bt_period_{MARKET}_2018-01-01_2024-01-01.parquet")
    df["date"] = pd.to_datetime(df["date"])
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
    df[feats] = zscore(df, feats)
    dates = np.sort(df["date"].unique())
    reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
    print(f"\n{MARKET}: {len(df)} rows, {len(reb)} rebalances, cost {COST[MARKET]*1e4:.0f}bps one-way", flush=True)

    smoothed = {a: {} for a in EWMAS}        # ticker -> smoothed rank pct
    prevset = {a: None for a in EWMAS}        # ticker set of prev top decile
    rows = []
    for k in reb:
        t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=[TGT])
        te = df[df["date"] == t].dropna(subset=[RET])
        if len(tr) < 5000 or len(te) < 30:
            continue
        sw = np.abs(tr["mn"].values)   # per-market tradeable label (matches wf_kr_levers/production mn)
        m = lgb.LGBMRegressor(**LGB).fit(tr[feats].astype(float), tr["mn"].astype(float), sample_weight=sw)
        p = m.predict(te[feats].astype(float))
        tick = te["ticker"].values
        yreal = pd.to_numeric(te[RET], errors="coerce").values
        um = np.nanmean(yreal)
        rawrank = pd.Series(p).rank(pct=True).values      # 0..1
        rec = {"date": pd.Timestamp(t).date()}
        for a in EWMAS:
            sm = np.array([a * rawrank[i] + (1 - a) * smoothed[a].get(tick[i], rawrank[i])
                           for i in range(len(tick))])
            for i in range(len(tick)):
                smoothed[a][tick[i]] = sm[i]
            thr = np.quantile(sm, DEC)
            sel = sm >= thr
            cur = set(tick[sel])
            rec[f"gross_{a}"] = float(np.nanmean(yreal[sel]) - um) if sel.sum() else np.nan
            if prevset[a] is not None and cur and prevset[a]:
                rec[f"turn_{a}"] = 1.0 - len(cur & prevset[a]) / len(cur | prevset[a])
            else:
                rec[f"turn_{a}"] = np.nan
            prevset[a] = cur
        rows.append(rec)

    r = pd.DataFrame(rows)
    r.to_csv(f"var/_analysis/wf_b5_{MARKET}.csv", index=False)
    wpy = 252.0 / STEP; c = COST[MARKET]
    print(f"{'variant':9s} {'grossExc':>9s} {'meanTurn':>9s} {'netExc':>8s} {'netSharpe':>10s} {'net>0%':>7s} {'winVsRaw':>9s}")
    net_raw = (r["gross_1.0"] - r["turn_1.0"].fillna(0) * 2 * c)
    for a in EWMAS:
        g = r[f"gross_{a}"]; turn = r[f"turn_{a}"]
        net = g - turn.fillna(0) * 2 * c
        nn = net.dropna()
        sh = nn.mean() / nn.std() * np.sqrt(wpy) if nn.std() > 0 else 0
        win = (net > net_raw).mean() if a != 1.0 else np.nan
        ws = f"{win:.0%}" if not np.isnan(win) else "  -"
        lab = "raw" if a == 1.0 else f"ewma{a}"
        print(f"{lab:9s} {g.mean()*100:+8.3f}% {turn.mean():9.2f} {net.mean()*100:+7.3f}% "
              f"{sh:+10.2f} {(net>0).mean():6.0%} {ws:>9s}", flush=True)
    print(f"  adopt smoothing only if netExc rises vs raw AND grossExc not materially lost.", flush=True)
