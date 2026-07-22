"""Cross-asset betas as NEW orthogonal features on clean liquid KR. Macro LEVELS
are cross-sectionally constant (useless for selection), but per-STOCK rolling
betas to USDKRW / semis / Korea-ETF / copper ARE dispersed across the cross-
section (exporters, semi-supply-chain, China-exposed names differ). Adds
beta_* (126d) to the baseline price features and tests on px_{UNI}_PYKRX.

Usage: uv run python var/_analysis/kr_betas.py KR_MID_PYKRX
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb, yfinance as yf
from scipy.stats import spearmanr
from pathlib import Path

UNI = sys.argv[1] if len(sys.argv) > 1 else "KR_MID_PYKRX"
STEP, TRAIN_MIN, TRAIL, EMB, DEC, W = 63, 504, 756, 21, 0.9, 126
SEEDS = [0, 1, 2]
LGB = dict(n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=50,
           subsample=0.7, colsample_bytree=0.7, reg_lambda=5.0, n_jobs=-1, verbose=-1)
MACRO = {"usdkrw": "KRW=X", "sox": "^SOX", "ewy": "EWY", "copper": "HG=F"}

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
px["hi_252"] = gg["close"].apply(lambda s: s / (s.rolling(252).max() + 1e-9))
BASE = ["ret_5", "ret_10", "ret_21", "ret_63", "ret_126", "ret_252", "mom_12_1", "vol_21", "vol_63",
        "vol_252", "mom_vadj", "px_vs_sma20", "px_vs_sma50", "px_vs_sma200", "sma50_200", "rsi14", "dv_21", "amihud", "hi_252"]

# macro daily returns, aligned to the KR trading-date grid (BUGFIX: ffill prices onto KR
# dates so US-calendar macros aren't sparse → previously gave 0% coverage & a false null)
grid = pd.DatetimeIndex(np.sort(px["date"].unique()))
mret = {}
for name, sym in MACRO.items():
    try:
        raw = yf.download(sym, start="2017-01-01", end="2026-07-05", auto_adjust=True, progress=False)
        s = raw["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s.index = pd.to_datetime(s.index)
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_localize(None)
        s = s[~s.index.duplicated()].reindex(grid.union(s.index)).ffill().reindex(grid)  # ffill onto KR grid
        mret[name] = s.pct_change().rename(name)
    except Exception as e:
        print(f"macro {name} fail {e}")
mdf = pd.concat(mret.values(), axis=1); mdf.index.name = "date"; mdf = mdf.reset_index()
px = px.merge(mdf, on="date", how="left")
gg = px.groupby("ticker", group_keys=False)   # RE-group after merge (px is new frame)
BETAS = []
for name in mret:   # only successfully-built macros
    cov = gg.apply(lambda x: x["_dret"].rolling(W).cov(x[name])).values
    var = px.groupby("ticker")[name].transform(lambda s: s.rolling(W).var()).values
    px[f"beta_{name}"] = cov / (var + 1e-12); BETAS.append(f"beta_{name}")
print(f"[{UNI}] macro betas built: {BETAS}, per-beta coverage: "
      + ", ".join(f"{b.split('_')[1]}={px[b].notna().mean():.0%}" for b in BETAS), flush=True)

px["fwd21"] = gg["close"].apply(lambda s: s.pct_change(21).shift(-21))
px["mn"] = px["fwd21"] - px.groupby("date")["fwd21"].transform("mean")
allf = BASE + BETAS
gd = px.groupby("date"); px[allf] = (px[allf] - gd[allf].transform("mean")) / (gd[allf].transform("std") + 1e-9)
dates = np.sort(px["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))

def excf(p, y):
    sel = p >= np.quantile(p, DEC); return (np.nanmean(y[sel]) - np.nanmean(y)) if sel.sum() else np.nan

rows = {"base": [], "base+beta": []}; ics = {"base": [], "base+beta": []}; a5sel = []
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = px[(px["date"] <= cut) & (px["date"] > lo)].dropna(subset=["mn"]); te = px[px["date"] == t].dropna(subset=["fwd21"])
    if len(tr) < 3000 or len(te) < 30:
        continue
    y = pd.to_numeric(te["fwd21"], errors="coerce").values; sw = np.abs(tr["mn"].values)
    for name, feats in (("base", BASE), ("base+beta", allf)):
        ps = [lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[feats].astype(float), tr["mn"], sample_weight=sw).predict(te[feats].astype(float)) for s in SEEDS]
        p = np.mean(ps, axis=0); rows[name].append(excf(p, y)); ics[name].append(spearmanr(p, te["mn"].values, nan_policy="omit").correlation)

print(f"\n===== {UNI} CROSS-ASSET BETAS ({len(rows['base'])} folds, 3-seed) =====")
for name in ("base", "base+beta"):
    e = np.array(rows[name]); print(f"  {name:10s} ic={np.nanmean(ics[name]):+.4f}  meanExc={e.mean()*100:+.2f}%  win>0={ (e>0).mean():.0%}")
d = np.array(rows["base+beta"]) - np.array(rows["base"])
print(f"  delta(beta-base): meanExc {d.mean()*100:+.2f}%  win {(d>0).mean():.0%}   adopt if >0 both.")
