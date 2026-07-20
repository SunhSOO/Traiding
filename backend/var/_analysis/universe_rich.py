"""Step 1 — richer feature model on the cap spectrum (reuses cached prices from
universe_alpha_test.py, no re-fetch). Expands the lean 20 price features to ~45
(more momentum/reversal horizons, several vol estimators, trend/RSI/Bollinger/
MACD, volume & liquidity, range/microstructure, 52w proximity) — a proxy for the
production feature richness on the price side. Tests whether a richer model lifts
the small-cap IC and preserves the large->small gradient. Same rigorous bar
(walk-forward top-decile EXCESS, 3-seed, sub-period IC).

Usage: uv run python var/_analysis/universe_rich.py US_SMALL
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from pathlib import Path

UNI = sys.argv[1]
STEP, TRAIN_MIN, TRAIL, EMB, DEC = 63, 504, 756, 21, 0.9
SEEDS = [0, 1, 2]
LGB = dict(n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=50,
           subsample=0.7, colsample_bytree=0.7, reg_lambda=5.0, n_jobs=-1, verbose=-1)
px = pd.read_parquet(Path(f"var/_analysis/px_{UNI}.parquet"))
px["date"] = pd.to_datetime(px["date"])
px = px.sort_values(["ticker", "date"])
g = px.groupby("ticker", group_keys=False)
FEATS = []


def add(name, series):
    px[name] = series.values if hasattr(series, "values") else series
    FEATS.append(name)


dret = g["close"].apply(lambda s: s.pct_change(1)); px["_dret"] = dret.values
gg = px.groupby("ticker", group_keys=False)
for n in (5, 10, 21, 42, 63, 126, 189, 252):
    add(f"ret_{n}", g["close"].apply(lambda s: s.pct_change(n)))
add("mom_12_1", px["ret_252"] - px["ret_21"])
add("mom_6_1", px["ret_126"] - px["ret_21"])
add("st_rev", -px["ret_21"])
for n in (10, 21, 63, 126, 252):
    add(f"vol_{n}", gg["_dret"].apply(lambda s: s.rolling(n).std()))
add("vol_ratio", px["vol_21"] / (px["vol_63"] + 1e-9))
add("parkinson", gg.apply(lambda d: (np.log(d["high"] / d["low"]) ** 2).rolling(21).mean()).values)
add("mom_vadj63", px["ret_63"] / (px["vol_63"] * np.sqrt(63) + 1e-9))
add("mom_vadj252", px["ret_252"] / (px["vol_252"] * np.sqrt(252) + 1e-9))
c = px["close"]
for w in (10, 20, 50, 100, 200):
    sma = gg["close"].apply(lambda s: s.rolling(w).mean())
    add(f"px_vs_sma{w}", c.values / (sma.values + 1e-9) - 1.0)
add("sma20_50", gg["close"].apply(lambda s: s.rolling(20).mean() / (s.rolling(50).mean() + 1e-9) - 1.0))
add("sma50_200", gg["close"].apply(lambda s: s.rolling(50).mean() / (s.rolling(200).mean() + 1e-9) - 1.0))
for n in (7, 14, 21):
    up = gg["_dret"].apply(lambda s: s.clip(lower=0).rolling(n).mean())
    dn = gg["_dret"].apply(lambda s: (-s.clip(upper=0)).rolling(n).mean())
    add(f"rsi{n}", 100 - 100 / (1 + up.values / (dn.values + 1e-9)))
add("boll_pos", gg["close"].apply(lambda s: (s - s.rolling(20).mean()) / (s.rolling(20).std() + 1e-9)))
ema12 = gg["close"].apply(lambda s: s.ewm(span=12).mean()); ema26 = gg["close"].apply(lambda s: s.ewm(span=26).mean())
macd = (ema12.values - ema26.values) / (c.values + 1e-9); px["_macd"] = macd
add("macd", macd)
add("macd_sig", gg["_macd"].apply(lambda s: s.ewm(span=9).mean()) if "_macd" in px else 0)
px["dollarvol"] = (px["close"] * px["volume"]).astype(float)
add("dv_21", gg["dollarvol"].apply(lambda s: s.rolling(21).mean()))
add("dv_ratio", gg["dollarvol"].apply(lambda s: s.rolling(21).mean() / (s.rolling(63).mean() + 1e-9)))
add("vol_z", gg["volume"].apply(lambda s: (s - s.rolling(63).mean()) / (s.rolling(63).std() + 1e-9)))
add("amihud21", gg.apply(lambda d: (d["_dret"].abs() / (d["dollarvol"] + 1e3)).rolling(21).mean()).values)
add("amihud63", gg.apply(lambda d: (d["_dret"].abs() / (d["dollarvol"] + 1e3)).rolling(63).mean()).values)
add("hl_range", gg.apply(lambda d: ((d["high"] - d["low"]) / d["close"]).rolling(21).mean()).values)
add("clv", gg.apply(lambda d: ((2 * d["close"] - d["high"] - d["low"]) / (d["high"] - d["low"] + 1e-9)).rolling(10).mean()).values)
add("hi_252", gg["close"].apply(lambda s: s / (s.rolling(252).max() + 1e-9)))
add("lo_252", gg["close"].apply(lambda s: s / (s.rolling(252).min() + 1e-9)))

px["fwd21"] = gg["close"].apply(lambda s: s.pct_change(21).shift(-21))
px["mn"] = px["fwd21"] - px.groupby("date")["fwd21"].transform("mean")
gd = px.groupby("date")
px[FEATS] = (px[FEATS] - gd[FEATS].transform("mean")) / (gd[FEATS].transform("std") + 1e-9)
dates = np.sort(px["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
print(f"[{UNI}] {px['ticker'].nunique()} tickers, {len(FEATS)} rich feats, {len(reb)} folds", flush=True)


def exc(p, y):
    sel = p >= np.quantile(p, DEC)
    return (np.nanmean(y[sel]) - np.nanmean(y)) if sel.sum() else np.nan


rows = {s: [] for s in SEEDS}; ics = []
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = px[(px["date"] <= cut) & (px["date"] > lo)].dropna(subset=["mn"])
    te = px[px["date"] == t].dropna(subset=["fwd21"])
    if len(tr) < 3000 or len(te) < 30:
        continue
    y = pd.to_numeric(te["fwd21"], errors="coerce").values; sw = np.abs(tr["mn"].values)
    for s in SEEDS:
        m = lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[FEATS].astype(float), tr["mn"], sample_weight=sw)
        p = m.predict(te[FEATS].astype(float)); rows[s].append(exc(p, y))
        if s == 0:
            ics.append(spearmanr(p, te["mn"].values, nan_policy="omit").correlation)
wpy = 252.0 / STEP
per = [pd.Series(rows[s]).dropna() for s in SEEDS]
mean = np.mean([x.mean() for x in per]); sh = np.mean([x.mean() / x.std() * np.sqrt(wpy) if x.std() > 0 else 0 for x in per])
pos = np.mean([(x > 0).mean() for x in per]); ic = np.nanmean(ics)
icser = pd.Series(ics); h = len(icser) // 2
print(f"\n===== {UNI} RICH ({len(FEATS)} feats): {len(per[0])} folds =====")
print(f"  meanExcess(3-seed)={mean*100:+.3f}%  ExcSharpe={sh:+.2f}  Exc>0%={pos:.0%}  meanIC={ic:+.4f}  "
      f"IC H1/H2={icser.iloc[:h].mean():+.4f}/{icser.iloc[h:].mean():+.4f}")
