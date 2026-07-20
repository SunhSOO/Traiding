"""Re-test the model-structure levers that were REJECTED on the efficient
large-cap universe, now on the RIGHT (clean pykrx liquid KR) universe where a
real signal exists. The audit's central hypothesis: 'rejected on large-caps'
proves nothing about the inefficient universe. 3-seed + adversarial (best-2, bear).

Variants vs the pure-lgbm baseline:
  ens_ridge   : rank-average of lgbm + Ridge (the rejected EnsembleRankModel idea)
  regime      : two lgbms split by trailing vol regime (vix-less proxy: universe
                mean vol_21 median), route test date to its regime's model (B1 idea)
  ewma05      : EWMA(rank) smoothing alpha=0.5 (the rejected KR EWMA idea)

Usage: uv run python var/_analysis/kr_model_levers.py KR_MID_PYKRX
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.linear_model import Ridge
from scipy.stats import spearmanr
from pathlib import Path

UNI = sys.argv[1] if len(sys.argv) > 1 else "KR_MID_PYKRX"
STEP, TRAIN_MIN, TRAIL, EMB, DEC = 63, 504, 756, 21, 0.9
SEEDS = [0, 1, 2]
LGB = dict(n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=50,
           subsample=0.7, colsample_bytree=0.7, reg_lambda=5.0, n_jobs=-1, verbose=-1)

px = pd.read_parquet(Path(f"var/_analysis/px_{UNI}.parquet"))
px["date"] = pd.to_datetime(px["date"]); px = px.sort_values(["ticker", "date"])
g = px.groupby("ticker", group_keys=False)
def rr(n): return g["close"].apply(lambda s: s.pct_change(n))
for n in (5, 10, 21, 63, 126, 252):
    px[f"ret_{n}"] = rr(n)
px["mom_12_1"] = px["ret_252"] - px["ret_21"]
d1 = g["close"].apply(lambda s: s.pct_change(1)); px["_dret"] = d1.values
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
px["amihud"] = gg.apply(lambda x: (x["_dret"].abs() / (x["dollarvol"] + 1e3)).rolling(21).mean()).values
px["hl_range"] = gg.apply(lambda x: ((x["high"] - x["low"]) / x["close"]).rolling(21).mean()).values
px["hi_252"] = gg["close"].apply(lambda s: s / (s.rolling(252).max() + 1e-9))
FEATS = ["ret_5", "ret_10", "ret_21", "ret_63", "ret_126", "ret_252", "mom_12_1", "vol_21", "vol_63",
         "vol_252", "mom_vadj", "px_vs_sma20", "px_vs_sma50", "px_vs_sma200", "sma50_200", "rsi14",
         "dv_21", "amihud", "hl_range", "hi_252"]
gd = px.groupby("date"); px[FEATS] = (px[FEATS] - gd[FEATS].transform("mean")) / (gd[FEATS].transform("std") + 1e-9)
px["fwd21"] = gg["close"].apply(lambda s: s.pct_change(21).shift(-21))
px["mn"] = px["fwd21"] - px.groupby("date")["fwd21"].transform("mean")
px["_mktvol"] = px.groupby("date")["vol_21"].transform("mean")
dates = np.sort(px["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))

def excf(p, y):
    sel = p >= np.quantile(p, DEC)
    return (np.nanmean(y[sel]) - np.nanmean(y)) if sel.sum() else np.nan
def prep(X):
    return pd.DataFrame(X).replace([np.inf, -np.inf], np.nan).clip(-10, 10).fillna(0).to_numpy(float)

VARIANTS = ["lgbm", "ens_ridge", "regime", "ewma05"]
rows = {v: [] for v in VARIANTS}; ic = {v: [] for v in VARIANTS}; bench = []; state = {}
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = px[(px["date"] <= cut) & (px["date"] > lo)].dropna(subset=["mn"])
    te = px[px["date"] == t].dropna(subset=["fwd21"])
    if len(tr) < 3000 or len(te) < 30:
        continue
    y = pd.to_numeric(te["fwd21"], errors="coerce").values; sw = np.abs(tr["mn"].values); tk = te["ticker"].values
    bench.append(float(np.nanmean(y)))
    med = tr["_mktvol"].median(); calm = tr[tr["_mktvol"] <= med]; stress = tr[tr["_mktvol"] > med]
    t_stress = bool(te["_mktvol"].median() > med)
    for v in VARIANTS:
        ps = []
        for s in SEEDS:
            lg = lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[FEATS].astype(float), tr["mn"], sample_weight=sw)
            if v == "lgbm" or v == "ewma05":
                ps.append(lg.predict(te[FEATS].astype(float)))
            elif v == "ens_ridge":
                rg = Ridge(alpha=10).fit(prep(tr[FEATS]), tr["mn"], sample_weight=sw)
                pr = rg.predict(prep(te[FEATS]))
                ps.append(pd.Series(lg.predict(te[FEATS].astype(float))).rank().values + pd.Series(pr).rank().values)
            elif v == "regime":
                sub = stress if t_stress else calm
                mm = lgb.LGBMRegressor(**LGB, random_state=s).fit(sub[FEATS].astype(float), sub["mn"], sample_weight=np.abs(sub["mn"].values))
                ps.append(mm.predict(te[FEATS].astype(float)))
        p = np.mean(ps, axis=0)
        if v == "ewma05":
            rk = pd.Series(p).rank(pct=True).values
            rk = np.array([0.5 * rk[i] + 0.5 * state.get((v, tk[i]), rk[i]) for i in range(len(tk))])
            for i in range(len(tk)):
                state[(v, tk[i])] = rk[i]
            p = rk
        rows[v].append(excf(p, y)); ic[v].append(spearmanr(p, te["mn"].values, nan_policy="omit").correlation)

bench = np.array(bench); bear = bench <= np.quantile(bench, 1/3)
print(f"\n===== {UNI} MODEL LEVERS ({len(bench)} folds, 3-seed) =====")
print(f"{'variant':10s} {'ic':>7s} {'meanExc':>8s} {'best-2':>8s} {'bear':>7s} {'win':>5s} {'vsBase':>7s}")
base = np.array(rows["lgbm"])
for v in VARIANTS:
    e = np.array(rows[v]); b2 = np.mean(np.sort(e)[:-2]); win = (e > base).mean() if v != "lgbm" else np.nan
    ws = f"{win:.0%}" if v != "lgbm" else "  -"
    print(f"{v:10s} {np.nanmean(ic[v]):+7.4f} {e.mean()*100:+7.2f}% {b2*100:+7.2f}% {np.nanmean(e[bear])*100:+6.2f}% "
          f"{(e>0).mean():4.0%} {ws:>7s}", flush=True)
print("  adopt a lever only if it beats lgbm on excess across MOST folds AND holds best-2/bear.")
