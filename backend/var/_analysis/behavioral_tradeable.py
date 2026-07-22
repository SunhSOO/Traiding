"""TEST 2 — are the gradient-confirmed behavioral signals TRADEABLE net-of-cost?

The gradient test showed REV_1M/ILLIQ/LOTTO strengthen down the efficiency gradient
(KR_MICRO gross IC 0.07-0.145). But gross IC != tradeable: the illiquidity premium is
partly compensation FOR illiquidity (high micro-cap costs). This tests top-decile
long-vs-universe EXCESS net-of-realistic-cost, with turnover, best-2, bear — per
universe at its own cost level. A signal is REAL-AND-TRADEABLE only if net excess > 0
after that universe's cost and it survives best-2/bear.

Universes×cost(bps/side): KR_MID=40, KR_MICRO=120 (wide spread), US_LARGE=15 (contrast).
Signals: REV_1M, ILLIQ, LOTTO, COMPOSITE (mean of per-date z of the three).
Usage: uv run python var/_analysis/behavioral_tradeable.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy.stats import spearmanr

UNIS = [("KR_MID", "px_KR_MID_PYKRX", 40), ("KR_MICRO", "px_KR_MICRO_PYKRX", 120), ("US_LARGE", "px_US_LARGE", 15)]
SIGS = ["REV_1M", "ILLIQ", "LOTTO", "COMPOSITE"]
STEP, DEC = 21, 0.9


def signals(df):
    df = df.sort_values(["ticker", "date"]).copy()
    g = df.groupby("ticker", group_keys=False)
    df["ret1"] = g["close"].pct_change()
    df["ret21"] = g["close"].pct_change(21)
    dv = (df["close"] * df["volume"]).replace(0, np.nan)
    df["ai1"] = df["ret1"].abs() / dv
    df["ILLIQ"] = g["ai1"].transform(lambda s: s.rolling(21, min_periods=10).mean())
    df["LOTTO"] = -g["ret1"].transform(lambda s: s.rolling(21, min_periods=10).max())
    df["REV_1M"] = -df["ret21"]
    df["fwd"] = g["close"].shift(-21) / df["close"] - 1
    return df


def zc(s):  # cross-section z
    return (s - s.mean()) / (s.std() + 1e-9)


def walk(df, sig, bps):
    dates = np.sort(df["date"].unique()); samp = dates[252::STEP]
    e, turns, bench = [], [], []; prev = None
    for t in samp:
        d = df[df["date"] == t].dropna(subset=["fwd"]).copy()
        if sig == "COMPOSITE":
            for c in ["REV_1M", "ILLIQ", "LOTTO"]:
                d[c + "_z"] = zc(d[c])
            d["S"] = d[["REV_1M_z", "ILLIQ_z", "LOTTO_z"]].mean(axis=1)
        else:
            d["S"] = d[sig]
        d = d.dropna(subset=["S"])
        if len(d) < 25:
            continue
        thr = d["S"].quantile(DEC); sel = d[d["S"] >= thr]
        y = d["fwd"].values
        e.append(float(sel["fwd"].mean() - np.nanmean(y)))
        cur = set(sel["ticker"]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else np.nan); prev = cur
        bench.append(float(np.nanmean(y)))
    e = np.array(e); bench = np.array(bench); turn = np.nanmean(turns)
    gross = e.mean(); net = gross - (turn if turn == turn else 0) * 2 * bps / 1e4
    b2 = np.mean(np.sort(e)[:-2]); bear = np.nanmean(e[bench <= np.quantile(bench, 1/3)])
    ppy = 252.0 / STEP
    return dict(gross=gross, net=net, net_ann=net * ppy, turn=turn, b2=b2, bear=bear, pos=(e > 0).mean(), n=len(e))


print(f"{'universe':10s} {'cost':>5s} {'signal':11s} {'grossExc':>9s} {'netExc':>8s} {'net_ANN':>8s} {'turn':>5s} {'best-2':>8s} {'bear':>7s} {'pos':>4s}")
for name, path, bps in UNIS:
    df = pd.read_parquet(f"var/_analysis/{path}.parquet")
    df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
    df = signals(df)
    for sig in SIGS:
        r = walk(df, sig, bps)
        flag = "  <== net+ & best-2+ & bear+" if (r["net"] > 0 and r["b2"] > 0 and r["bear"] > 0) else ""
        print(f"{name:10s} {bps:4d}b {sig:11s} {r['gross']*100:+8.2f}% {r['net']*100:+7.2f}% {r['net_ann']*100:+7.1f}% "
              f"{r['turn']:4.0%} {r['b2']*100:+7.2f}% {r['bear']*100:+6.2f}% {r['pos']:3.0%}{flag}", flush=True)
    print()
