"""Kill-or-confirm adversarial battery on the CONFIRMED KR small/micro edge —
the SAME gauntlet that killed the US ensemble / EWMA / B1 winners, which
KR_MICRO has NOT yet faced (it passed only 3-seed + H1/H2). Reuses cached
prices (px_KR_*.parquet, no re-fetch). Lean 20-feature price model.

Reports per universe (3-seed):
  meanExc, meanIC, IC H1/H2                (baseline, already known)
  best2drop   = mean excess with the 2 best folds removed (concentration)
  conc5       = share of total positive excess from the top-5 folds
  bear        = mean excess on bottom-tercile market-return folds (regime)
  worst_fold, winrate

A confirmed edge should keep positive excess after best-2 drop, have conc5 well
below ~0.7, and not collapse (or go negative) on bear folds.

Usage: uv run python var/_analysis/wf_kr_adversarial.py KR_MICRO
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from pathlib import Path

UNI = sys.argv[1] if len(sys.argv) > 1 else "KR_MICRO"
STEP, TRAIN_MIN, TRAIL, EMB, DEC = 63, 504, 756, 21, 0.9
SEEDS = [0, 1, 2]
LGB = dict(n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=50,
           subsample=0.7, colsample_bytree=0.7, reg_lambda=5.0, n_jobs=-1, verbose=-1)

px = pd.read_parquet(Path(f"var/_analysis/px_{UNI}.parquet"))
px["date"] = pd.to_datetime(px["date"])
px = px.sort_values(["ticker", "date"])
g = px.groupby("ticker", group_keys=False)
def ret(n): return g["close"].apply(lambda s: s.pct_change(n))
for n in (5, 10, 21, 63, 126, 252):
    px[f"ret_{n}"] = ret(n)
px["mom_12_1"] = px["ret_252"] - px["ret_21"]
dret = g["close"].apply(lambda s: s.pct_change(1)); px["_dret"] = dret.values
gg = px.groupby("ticker", group_keys=False)
for n in (21, 63, 252):
    px[f"vol_{n}"] = gg["_dret"].apply(lambda s: s.rolling(n).std())
px["mom_vadj"] = px["ret_63"] / (px["vol_63"] * np.sqrt(63) + 1e-9)
c = px["close"]
for w in (20, 50, 200):
    sma = gg["close"].apply(lambda s: s.rolling(w).mean()); px[f"px_vs_sma{w}"] = c.values / (sma.values + 1e-9) - 1
px["sma50_200"] = gg["close"].apply(lambda s: s.rolling(50).mean() / (s.rolling(200).mean() + 1e-9) - 1)
up = gg["_dret"].apply(lambda s: s.clip(lower=0).rolling(14).mean()); dn = gg["_dret"].apply(lambda s: (-s.clip(upper=0)).rolling(14).mean())
px["rsi14"] = 100 - 100 / (1 + up.values / (dn.values + 1e-9))
px["dollarvol"] = (px["close"] * px["volume"]).astype(float)
px["dv_21"] = gg["dollarvol"].apply(lambda s: s.rolling(21).mean())
px["amihud"] = gg.apply(lambda d: (d["_dret"].abs() / (d["dollarvol"] + 1e3)).rolling(21).mean()).values
px["hl_range"] = gg.apply(lambda d: ((d["high"] - d["low"]) / d["close"]).rolling(21).mean()).values
px["hi_252"] = gg["close"].apply(lambda s: s / (s.rolling(252).max() + 1e-9))
FEATS = ["ret_5", "ret_10", "ret_21", "ret_63", "ret_126", "ret_252", "mom_12_1",
         "vol_21", "vol_63", "vol_252", "mom_vadj", "px_vs_sma20", "px_vs_sma50",
         "px_vs_sma200", "sma50_200", "rsi14", "dv_21", "amihud", "hl_range", "hi_252"]
px["fwd21"] = gg["close"].apply(lambda s: s.pct_change(21).shift(-21))
px["mn"] = px["fwd21"] - px.groupby("date")["fwd21"].transform("mean")
gd = px.groupby("date"); px[FEATS] = (px[FEATS] - gd[FEATS].transform("mean")) / (gd[FEATS].transform("std") + 1e-9)
dates = np.sort(px["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))

def excf(p, y):
    sel = p >= np.quantile(p, DEC)
    return (np.nanmean(y[sel]) - np.nanmean(y)) if sel.sum() else np.nan

rows = {s: [] for s in SEEDS}; ics = []; bench = []
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = px[(px["date"] <= cut) & (px["date"] > lo)].dropna(subset=["mn"])
    te = px[px["date"] == t].dropna(subset=["fwd21"])
    if len(tr) < 3000 or len(te) < 30:
        continue
    y = pd.to_numeric(te["fwd21"], errors="coerce").values; sw = np.abs(tr["mn"].values)
    bench.append(float(np.nanmean(y)))
    for s in SEEDS:
        m = lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[FEATS].astype(float), tr["mn"], sample_weight=sw)
        p = m.predict(te[FEATS].astype(float)); rows[s].append(excf(p, y))
        if s == 0:
            ics.append(spearmanr(p, te["mn"].values, nan_policy="omit").correlation)

exc = pd.DataFrame(rows).mean(axis=1).values            # per-fold excess (seed-averaged)
bench = np.array(bench); ic = np.array(ics)
n = len(exc)
mean = np.nanmean(exc)
best2 = np.mean(np.sort(exc)[:-2]) if n > 2 else np.nan   # drop 2 best folds
pos = exc[exc > 0].sum()
conc5 = (np.sort(exc)[::-1][:5].clip(min=0).sum() / pos) if pos > 0 else np.nan
bear_mask = bench <= np.quantile(bench, 1/3)
bear = np.nanmean(exc[bear_mask]) if bear_mask.sum() else np.nan
h = n // 2
print(f"\n===== {UNI} ADVERSARIAL BATTERY ({n} folds, 3-seed) =====")
print(f"  meanExcess     = {mean*100:+.3f}%    meanIC = {ic.mean():+.4f}   IC H1/H2 = {ic[:h].mean():+.4f}/{ic[h:].mean():+.4f}")
print(f"  best-2-dropped = {best2*100:+.3f}%   ({'SURVIVES' if best2>0 else 'COLLAPSES'})")
print(f"  conc5 (top-5 fold share of +excess) = {conc5:.0%}   ({'ok' if conc5<0.7 else 'CONCENTRATED'})")
print(f"  bear folds ({int(bear_mask.sum())}) meanExcess = {bear*100:+.3f}%   ({'holds' if bear>0 else 'NEGATIVE in bear'})")
print(f"  worst fold = {np.nanmin(exc)*100:+.3f}%   winrate = {(exc>0).mean():.0%}")
print(f"  VERDICT: confirmed only if best-2-dropped>0 AND conc5<~0.7 AND bear not deeply negative.")
