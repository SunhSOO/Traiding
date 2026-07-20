"""Improvement-lever sweep on the REAL (clean pykrx, liquid KR) universe. Reuses
px_{UNI}_PYKRX.parquet. Tests, all 3-seed, net-of-cost, on the confirmed
data-source-robust buckets (KR_MID/KR_LARGE):

  (1) HORIZON: label = fwd_h return for h in {5,10,21,42}, rebalance STEP=h
      (non-overlapping), reported as ANNUALIZED net excess so horizons compare.
  (2) CONCENTRATION: at h=21, top-decile vs top-N {10,15,20} vs DEC 0.95.
  (3) SMOOTHING: at h=21, EWMA(rank) alpha {0.3,0.5} -> turnover cut -> net.
  (4) WEIGHTING: equal vs inverse-vol vs inverse-amihud top-decile.

Cost model: per-side bps by universe (MID 40 / LARGE 25), round-trip = turnover*2*bps.
Baseline (h=21, step=63, decile, equal, no smoothing) matches wf_kr_adversarial.

Usage: uv run python var/_analysis/kr_liquid_sweep.py KR_MID_PYKRX
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from pathlib import Path

UNI = sys.argv[1] if len(sys.argv) > 1 else "KR_MID_PYKRX"
TRAIN_MIN, TRAIL, DEC = 504, 756, 0.9
SEEDS = [0, 1, 2]
BPS = 25 if "LARGE" in UNI else 40   # per-side bps (round trip = turnover*2*bps)
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
# forward returns per horizon
for h in (5, 10, 21, 42):
    px[f"fwd{h}"] = gg["close"].apply(lambda s: s.pct_change(h).shift(-h))
px["_amihud_raw"] = px["amihud"]; px["_vol_raw"] = px["vol_21"]
dates = np.sort(px["date"].unique())


def walk(h, step, dec=DEC, topn=None, ewma=None, weight="equal"):
    reb = list(range(TRAIN_MIN, len(dates) - h, step))
    lab = f"fwd{h}"; mn = px[lab] - px.groupby("date")[lab].transform("mean")
    px["_mn"] = mn
    per_fold, turns, ics = [], [], []
    state, prev = {}, None
    for k in reb:
        t = dates[k]; cut = dates[k - h]; lo = dates[max(0, k - h - TRAIL)]
        tr = px[(px["date"] <= cut) & (px["date"] > lo)].dropna(subset=["_mn"])
        te = px[px["date"] == t].dropna(subset=[lab])
        if len(tr) < 3000 or len(te) < 30:
            continue
        y = pd.to_numeric(te[lab], errors="coerce").values; sw = np.abs(tr["_mn"].values); tk = te["ticker"].values
        preds = []
        for s in SEEDS:
            m = lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[FEATS].astype(float), tr["_mn"], sample_weight=sw)
            preds.append(m.predict(te[FEATS].astype(float)))
        p = np.mean(preds, axis=0)
        rank = pd.Series(p).rank(pct=True).values
        if ewma is not None:
            rank = np.array([ewma * rank[i] + (1 - ewma) * state.get(tk[i], rank[i]) for i in range(len(tk))])
            for i in range(len(tk)):
                state[tk[i]] = rank[i]
        if topn is not None:
            sel = rank >= np.sort(rank)[-topn] if topn < len(rank) else np.ones(len(rank), bool)
        else:
            sel = rank >= np.quantile(rank, dec)
        if weight == "invvol":
            w = 1.0 / (te["_vol_raw"].values[sel] + 1e-9)
        elif weight == "invamihud":
            w = 1.0 / (te["_amihud_raw"].values[sel] + 1e-12)
        else:
            w = np.ones(sel.sum())
        w = w / (w.sum() + 1e-12)
        exc = float(np.nansum(w * y[sel]) - np.nanmean(y))
        cur = set(tk[sel]); turn = (1 - len(cur & prev) / len(cur | prev)) if prev else np.nan; prev = cur
        per_fold.append(exc); turns.append(turn); ics.append(spearmanr(p, te["_mn"].values, nan_policy="omit").correlation)
    e = np.array(per_fold); turn = np.nanmean(turns); ic = np.nanmean(ics)
    ppy = 252.0 / step
    net = e.mean() - (turn if turn == turn else 0) * 2 * BPS / 1e4
    return dict(gross=e.mean(), net=net, net_ann=net * ppy, ic=ic, turn=turn,
                pos=(e > 0).mean(), sharpe=(e.mean() / e.std() * np.sqrt(ppy) if e.std() > 0 else 0), n=len(e))


print(f"\n===== {UNI}  (per-side {BPS}bps) =====")
print(f"{'variant':26s} {'ic':>7s} {'grossExc':>9s} {'turn':>5s} {'netExc':>8s} {'netANN':>8s} {'Sharpe':>7s} {'pos':>5s}")
def show(name, r):
    print(f"{name:26s} {r['ic']:+7.4f} {r['gross']*100:+8.2f}% {r['turn']:5.2f} {r['net']*100:+7.2f}% "
          f"{r['net_ann']*100:+7.1f}% {r['sharpe']:+7.2f} {r['pos']:4.0%}", flush=True)

# (1) horizon (non-overlapping step=h)
for h in (5, 10, 21, 42):
    show(f"H{h}/step{h} decile eq", walk(h, h))
# baseline 21/63 for continuity
show("H21/step63 decile eq(base)", walk(21, 63))
# (2) concentration @21/63
for tn in (10, 15, 20):
    show(f"H21/63 topN={tn} eq", walk(21, 63, topn=tn))
show("H21/63 DEC0.95 eq", walk(21, 63, dec=0.95))
# (3) smoothing @21/63
for a in (0.3, 0.5):
    show(f"H21/63 ewma{a} decile", walk(21, 63, ewma=a))
# (4) weighting @21/63
show("H21/63 decile invvol", walk(21, 63, weight="invvol"))
show("H21/63 decile invamihud", walk(21, 63, weight="invamihud"))
