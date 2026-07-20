"""Adversarial verification of the sweep's promising candidates on clean liquid
KR: baseline(H21/step63 equal decile) vs H42(longer horizon) vs invvol-weight vs
ewma0.5. Reports net + best-2-dropped + conc5 + bear for each — the gauntlet
that deflated every prior 'winner'. invvol's +6.96% (same IC) is pure weighting,
so conc5/best-2 will reveal if it's a real low-vol premium or a concentration
artifact.

Usage: uv run python var/_analysis/kr_verify.py KR_MID_PYKRX
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
BPS = 25 if "LARGE" in UNI else 40
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
for h in (21, 42):
    px[f"fwd{h}"] = gg["close"].apply(lambda s: s.pct_change(h).shift(-h))
px["_vol_raw"] = px["vol_21"]
dates = np.sort(px["date"].unique())


def walk(h, step, ewma=None, weight="equal"):
    reb = list(range(TRAIN_MIN, len(dates) - h, step))
    lab = f"fwd{h}"; px["_mn"] = px[lab] - px.groupby("date")[lab].transform("mean")
    e, turns, ics, bench = [], [], [], []
    state, prev = {}, None
    for k in reb:
        t = dates[k]; cut = dates[k - h]; lo = dates[max(0, k - h - TRAIL)]
        tr = px[(px["date"] <= cut) & (px["date"] > lo)].dropna(subset=["_mn"]); te = px[px["date"] == t].dropna(subset=[lab])
        if len(tr) < 3000 or len(te) < 30:
            continue
        y = pd.to_numeric(te[lab], errors="coerce").values; sw = np.abs(tr["_mn"].values); tk = te["ticker"].values
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[FEATS].astype(float), tr["_mn"], sample_weight=sw).predict(te[FEATS].astype(float)) for s in SEEDS], axis=0)
        rank = pd.Series(p).rank(pct=True).values
        if ewma is not None:
            rank = np.array([ewma * rank[i] + (1 - ewma) * state.get(tk[i], rank[i]) for i in range(len(tk))])
            for i in range(len(tk)):
                state[tk[i]] = rank[i]
        sel = rank >= np.quantile(rank, DEC)
        w = (1.0 / (te["_vol_raw"].values[sel] + 1e-9)) if weight == "invvol" else np.ones(sel.sum())
        w = w / (w.sum() + 1e-12)
        e.append(float(np.nansum(w * y[sel]) - np.nanmean(y)))
        cur = set(tk[sel]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else np.nan); prev = cur
        ics.append(spearmanr(p, te["_mn"].values, nan_policy="omit").correlation); bench.append(float(np.nanmean(y)))
    e = np.array(e); bench = np.array(bench); turn = np.nanmean(turns)
    b2 = np.mean(np.sort(e)[:-2]); pos = e[e > 0].sum(); conc5 = np.sort(e)[::-1][:5].clip(min=0).sum() / pos if pos > 0 else np.nan
    bear = np.nanmean(e[bench <= np.quantile(bench, 1/3)])
    net = e.mean() - (turn if turn == turn else 0) * 2 * BPS / 1e4
    return dict(ic=np.nanmean(ics), gross=e.mean(), net=net, b2=b2, conc5=conc5, bear=bear, turn=turn, pos=(e > 0).mean(), n=len(e))


print(f"\n===== {UNI} CANDIDATE VERIFICATION ({BPS}bps/side) =====")
print(f"{'variant':16s} {'ic':>7s} {'gross':>7s} {'net':>7s} {'best-2':>7s} {'conc5':>6s} {'bear':>7s} {'pos':>4s}")
for name, kw in [("base H21/63 eq", dict(h=21, step=63)),
                 ("H42/step42 eq", dict(h=42, step=42)),
                 ("H21/63 invvol", dict(h=21, step=63, weight="invvol")),
                 ("H21/63 ewma0.5", dict(h=21, step=63, ewma=0.5)),
                 ("H42 invvol", dict(h=42, step=42, weight="invvol"))]:
    r = walk(**kw)
    print(f"{name:16s} {r['ic']:+7.4f} {r['gross']*100:+6.2f}% {r['net']*100:+6.2f}% {r['b2']*100:+6.2f}% "
          f"{r['conc5']:5.0%} {r['bear']*100:+6.2f}% {r['pos']:3.0%}", flush=True)
print("  keep a lever only if best-2>0, conc5<~0.7, bear not deeply negative, net beats base.")
