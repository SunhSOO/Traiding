"""Re-validation of the KR EWMA rank-smoothing candidate (B5) BEFORE wiring it
into production. Confirms the 2020-2023 win is not (a) a lucky alpha, (b) one
sub-period, or (c) seed luck — the three ways in-sample findings die live.

For seeds x alphas, walk-forward the KR 6yr cache (mn label, production recipe):
select top decile by EWMA(alpha) of the model rank; report per-alpha (avg over
seeds): gross top-decile excess, NET @30bps, net Sharpe, winVsRaw fold-rate,
and FIRST-half vs SECOND-half net (temporal stability). Plus the per-seed net
spread at alpha=0.5.

Usage: uv run python var/_analysis/wf_b5_kr_robust.py
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from scripts.train_lgbm import ALL_FEATURE_COLS

RET = "ret_fwd_21d"
BASE = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=100,
            subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
STEP, TRAIN_MIN, TRAIL, EMB, DEC = 63, 504, 756, 21, 0.9
COST = 0.0030
ALPHAS = [1.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
SEEDS = [0, 1, 2]

df = pd.read_parquet("var/_bt_period_KR_2018-01-01_2024-01-01.parquet")
df["date"] = pd.to_datetime(df["date"])
feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
g = df.groupby("date")
df[feats] = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
dates = np.sort(df["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
print(f"KR: {len(df)} rows, {len(reb)} folds, seeds {SEEDS}, alphas {ALPHAS}", flush=True)

# rows[(seed,alpha)] -> list of per-fold dicts
rows = {(s, a): [] for s in SEEDS for a in ALPHAS}
state = {(s, a): {} for s in SEEDS for a in ALPHAS}     # ticker -> ewma rank
prev = {(s, a): None for s in SEEDS for a in ALPHAS}    # prev top-decile set
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["mn"])
    te = df[df["date"] == t].dropna(subset=[RET])
    if len(tr) < 5000 or len(te) < 30:
        continue
    sw = np.abs(tr["mn"].values); tick = te["ticker"].values
    yreal = pd.to_numeric(te[RET], errors="coerce").values; um = np.nanmean(yreal)
    for s in SEEDS:
        m = lgb.LGBMRegressor(**BASE, random_state=s).fit(
            tr[feats].astype(float), tr["mn"].astype(float), sample_weight=sw)
        rawrank = pd.Series(m.predict(te[feats].astype(float))).rank(pct=True).values
        for a in ALPHAS:
            st = state[(s, a)]
            sm = np.array([a * rawrank[i] + (1 - a) * st.get(tick[i], rawrank[i]) for i in range(len(tick))])
            for i in range(len(tick)):
                st[tick[i]] = sm[i]
            sel = sm >= np.quantile(sm, DEC); cur = set(tick[sel])
            gross = float(np.nanmean(yreal[sel]) - um) if sel.sum() else np.nan
            turn = (1.0 - len(cur & prev[(s, a)]) / len(cur | prev[(s, a)])) if (prev[(s, a)] and cur) else np.nan
            prev[(s, a)] = cur
            rows[(s, a)].append({"date": pd.Timestamp(t).date(), "gross": gross,
                                 "net": gross - (turn if turn == turn else 0.0) * 2 * COST, "turn": turn})

wpy = 252.0 / STEP
# aggregate across seeds per alpha
print(f"\n{'alpha':6s} {'gross':>8s} {'net@30':>8s} {'netSh':>7s} {'winVsRaw':>9s} {'net_H1':>8s} {'net_H2':>8s}")
raw_net = {s: pd.DataFrame(rows[(s, 1.0)])["net"] for s in SEEDS}
for a in ALPHAS:
    gs, ns, shs, wins, h1s, h2s = [], [], [], [], [], []
    for s in SEEDS:
        r = pd.DataFrame(rows[(s, a)])
        gs.append(r["gross"].mean()); ns.append(r["net"].mean())
        shs.append(r["net"].mean() / r["net"].std() * np.sqrt(wpy) if r["net"].std() > 0 else 0)
        wins.append((r["net"].values > raw_net[s].values).mean() if a != 1.0 else np.nan)
        h = len(r) // 2
        h1s.append(r["net"].iloc[:h].mean()); h2s.append(r["net"].iloc[h:].mean())
    lab = "raw" if a == 1.0 else f"{a}"
    ws = f"{np.mean(wins):.0%}" if a != 1.0 else "  -"
    print(f"{lab:6s} {np.mean(gs)*100:+7.2f}% {np.mean(ns)*100:+7.2f}% {np.mean(shs):+7.2f} "
          f"{ws:>9s} {np.mean(h1s)*100:+7.2f}% {np.mean(h2s)*100:+7.2f}%", flush=True)

print(f"\nper-seed net @alpha=0.5: " + ", ".join(
    f"seed{s}={pd.DataFrame(rows[(s,0.5)])['net'].mean()*100:+.2f}%" for s in SEEDS))
print(f"per-seed net @raw:       " + ", ".join(
    f"seed{s}={raw_net[s].mean()*100:+.2f}%" for s in SEEDS))
print("\nADOPT if: net>raw across MOST alphas (not one lucky a), winVsRaw>50%, "
      "H1 and H2 both positive-vs-raw (temporal), and all seeds agree in sign.")
