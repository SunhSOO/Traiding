"""Gate: does the H42 liquid-KR edge survive a REALIZABLE (tradeable) universe?
On clean pykrx KR_MID, apply liquidity floor (median $-vol pctile) + min-price
(close>=1000 KRW, drops penny/limit-prone names) and model limit-up no-fill
(don't buy names that gapped >= +29% into the decile — you can't fill at close on
상한가). H42 label/step. Reports net vs the unfiltered H42 baseline.

Usage: uv run python var/_analysis/kr_realizable.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from pathlib import Path

UNI = "KR_MID_PYKRX"; H, STEP, TRAIN_MIN, TRAIL, DEC, BPS = 42, 42, 504, 756, 0.9, 40
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
px["hi_252"] = gg["close"].apply(lambda s: s / (s.rolling(252).max() + 1e-9))
FEATS = ["ret_5", "ret_10", "ret_21", "ret_63", "ret_126", "ret_252", "mom_12_1", "vol_21", "vol_63",
         "vol_252", "mom_vadj", "px_vs_sma20", "px_vs_sma50", "px_vs_sma200", "sma50_200", "rsi14", "dv_21", "amihud", "hi_252"]
gd = px.groupby("date"); px[FEATS] = (px[FEATS] - gd[FEATS].transform("mean")) / (gd[FEATS].transform("std") + 1e-9)
px[f"fwd{H}"] = gg["close"].apply(lambda s: s.pct_change(H).shift(-H))
px["_close"] = px["close"]; px["_dv21"] = px["dollarvol"]
dates = np.sort(px["date"].unique())


def walk(liq=0.0, minpx=0, nofill_limit=False):
    reb = list(range(TRAIN_MIN, len(dates) - H, STEP))
    px["_mn"] = px[f"fwd{H}"] - px.groupby("date")[f"fwd{H}"].transform("mean")
    e, turns, bench = [], [], []; prev = None
    for k in reb:
        t = dates[k]; cut = dates[k - H]; lo = dates[max(0, k - H - TRAIL)]
        tr = px[(px["date"] <= cut) & (px["date"] > lo)].dropna(subset=["_mn"]); te = px[px["date"] == t].dropna(subset=[f"fwd{H}"])
        if len(tr) < 3000 or len(te) < 30:
            continue
        # realizable filters
        mask = np.ones(len(te), bool)
        if minpx > 0:
            mask &= (te["_close"].values >= minpx)
        if liq > 0:
            mask &= (te["_dv21"].values >= np.nanquantile(te["_dv21"].values, liq))
        if nofill_limit:
            mask &= (te["_dret"].values < 0.28)          # skip 상한가-gap names (can't fill at close)
        te = te[mask]
        if len(te) < 30:
            continue
        y = pd.to_numeric(te[f"fwd{H}"], errors="coerce").values; tk = te["ticker"].values
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[FEATS].astype(float), tr["_mn"], sample_weight=np.abs(tr["_mn"].values)).predict(te[FEATS].astype(float)) for s in SEEDS], axis=0)
        sel = p >= np.quantile(p, DEC)
        e.append(float(np.nanmean(y[sel]) - np.nanmean(y)))
        cur = set(tk[sel]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else np.nan); prev = cur
        bench.append(int(sel.sum()))
    e = np.array(e); turn = np.nanmean(turns); net = e.mean() - (turn if turn == turn else 0) * 2 * BPS / 1e4
    b2 = np.mean(np.sort(e)[:-2])
    return dict(gross=e.mean(), net=net, b2=b2, turn=turn, pos=(e > 0).mean(), names=np.mean(bench), n=len(e))


print(f"\n===== {UNI} REALIZABLE-UNIVERSE (H42, {BPS}bps) =====")
print(f"{'filter':30s} {'names':>6s} {'gross':>7s} {'net':>7s} {'best-2':>7s} {'turn':>5s} {'pos':>4s}")
for name, kw in [("H42 base (no filter)", {}),
                 ("+ minPx>=1000", dict(minpx=1000)),
                 ("+ liq floor 40%", dict(liq=0.4)),
                 ("+ minPx + liq40 + noLimitFill", dict(minpx=1000, liq=0.4, nofill_limit=True))]:
    r = walk(**kw)
    print(f"{name:30s} {r['names']:6.0f} {r['gross']*100:+6.2f}% {r['net']*100:+6.2f}% {r['b2']*100:+6.2f}% {r['turn']:5.2f} {r['pos']:3.0%}", flush=True)
print("  realizable = does net survive after dropping untradeable (penny/illiquid/limit-locked) names.")
