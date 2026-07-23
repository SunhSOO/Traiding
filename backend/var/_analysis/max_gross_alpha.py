"""G1-G4 — MAX GROSS ALPHA per universe (user directive 2026-07-23: map the gross-alpha
ceiling across ALL universes, then filter for tradeability later). For each of 9
universes, sweep a unified price-only signal library single-name AND an lgbm combine
(trailing walk-forward, no lookahead), report top-decile GROSS excess + rank-IC +
best-2 (drop 2 best folds = not one-fold luck) + pos%, and pick the MAX config.

Signals (oriented IC>0 = predicts higher fwd 21d return):
  STR=-ret5 REV_1M=-ret21 MOM=mom_12_1 ILLIQ=amihud LOTTO=-max_1m
  LOWVOL=-vol60 IVOL=-idiovol60 SIZE=-log($vol) HI52=close/hi252
GROSS (no cost) by explicit user choice; tradeability annotated separately in G5.
Usage: uv run python var/_analysis/max_gross_alpha.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr

UNIS = [  # (name, cache, source-tag) — efficiency high->low
    ("US_LARGE", "px_US_LARGE", "yf"), ("US_MID", "px_US_MID", "yf"),
    ("US_SMALL", "px_US_SMALL", "yf"), ("US_BROAD", "px_US_BROAD", "yf"),
    ("KR_LARGE", "px_KR_LARGE_PYKRX", "pykrx"), ("KR_MID", "px_KR_MID_PYKRX", "pykrx"),
    ("KR_SMALL", "px_KR_SMALL", "yf!"), ("KR_MICRO", "px_KR_MICRO_PYKRX", "pykrx"),
    ("TW_SMALL", "px_TW_SMALL", "yf!"),
]
SIGS = ["STR", "REV_1M", "MOM", "ILLIQ", "LOTTO", "LOWVOL", "IVOL", "SIZE", "HI52"]
STEP, DEC, TRAIL, TRAIN_MIN, EMB = 21, 0.9, 756, 504, 21
SEEDS = [0, 1]
LGB = dict(n_estimators=300, num_leaves=31, learning_rate=0.03, min_child_samples=80,
           subsample=0.7, colsample_bytree=0.7, reg_lambda=5.0, n_jobs=-1, verbose=-1)


def build(df):
    df = df.sort_values(["date", "ticker"]).copy()
    df["ret1"] = df.groupby("ticker")["close"].pct_change()
    df["mkt"] = df.groupby("date")["ret1"].transform("mean")
    df = df.sort_values(["ticker", "date"]); g = df.groupby("ticker", group_keys=False)
    df["STR"] = -g["close"].pct_change(5)
    df["REV_1M"] = -g["close"].pct_change(21)
    df["MOM"] = g["close"].shift(21) / g["close"].shift(252) - 1
    dv = (df["close"] * df["volume"]).replace(0, np.nan)
    df["ILLIQ"] = g.apply(lambda x: (x["ret1"].abs() / (x["close"] * x["volume"]).replace(0, np.nan)).rolling(21, min_periods=10).mean()).reset_index(level=0, drop=True)
    df["LOTTO"] = -g["ret1"].transform(lambda s: s.rolling(21, min_periods=10).max())
    df["LOWVOL"] = -g["ret1"].transform(lambda s: s.rolling(60, min_periods=30).std())
    df["resid"] = df["ret1"] - df["mkt"]
    df["IVOL"] = -g["resid"].transform(lambda s: s.rolling(60, min_periods=30).std())
    df["SIZE"] = -np.log(g.apply(lambda x: dv.loc[x.index].rolling(63, min_periods=20).median()).reset_index(level=0, drop=True).clip(lower=1))
    df["HI52"] = df["close"] / g["close"].transform(lambda s: s.rolling(252, min_periods=120).max())
    df["fwd"] = g["close"].shift(-21) / df["close"] - 1
    df["mn"] = df["fwd"] - df.groupby("date")["fwd"].transform("mean")
    return df


def agg(exc):
    e = np.array(exc)
    b2 = np.mean(np.sort(e)[:-2]) if len(e) > 2 else e.mean()
    return e.mean(), b2, (e > 0).mean(), len(e)


def sweep(df):
    dates = np.sort(df["date"].unique()); samp = dates[252::STEP]
    res = {}
    # single signals: per-date sort (no training)
    for s in SIGS:
        exc, ics = [], []
        for t in samp:
            d = df[df["date"] == t].dropna(subset=["fwd", s])
            if len(d) < 25:
                continue
            sel = d[d[s] >= d[s].quantile(DEC)]
            exc.append(float(sel["fwd"].mean() - d["fwd"].mean()))
            ics.append(spearmanr(d[s], d["fwd"]).correlation)
        if exc:
            m, b2, pos, n = agg(exc)
            res[s] = dict(exc=m, b2=b2, pos=pos, ic=np.nanmean(ics), n=n)
    # lgbm combine: trailing WF on the 9 signals, mn label
    reb = [k for k in range(TRAIN_MIN, len(dates) - EMB, STEP)]
    exc, ics = [], []
    for k in reb:
        t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["mn"] + SIGS)
        te = df[df["date"] == t].dropna(subset=["fwd"] + SIGS)
        if len(tr) < 3000 or len(te) < 25:
            continue
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=sd).fit(tr[SIGS], tr["mn"]).predict(te[SIGS]) for sd in SEEDS], axis=0)
        sel = p >= np.quantile(p, DEC)
        exc.append(float(te["fwd"].values[sel].mean() - te["fwd"].mean()))
        ics.append(spearmanr(p, te["fwd"]).correlation)
    if exc:
        m, b2, pos, n = agg(exc)
        res["lgbm9"] = dict(exc=m, b2=b2, pos=pos, ic=np.nanmean(ics), n=n)
    return res


print(f"{'universe':10s} {'src':6s} {'BEST config':12s} {'grossExc':>9s} {'best-2':>8s} {'pos':>4s} {'rankIC':>8s}   (top single & lgbm)")
summary = {}
for name, path, src in UNIS:
    try:
        df = pd.read_parquet(f"var/_analysis/{path}.parquet")
    except Exception as e:
        print(f"{name:10s} {src:6s} MISSING {e}"); continue
    df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
    df = build(df)
    r = sweep(df)
    if not r:
        print(f"{name:10s} {src:6s} (no folds)"); continue
    best = max(r.items(), key=lambda kv: kv[1]["exc"])
    summary[name] = (src, best, r)
    bk, bv = best
    others = sorted(r.items(), key=lambda kv: -kv[1]["exc"])[:3]
    tag = " ".join(f"{k}:{v['exc']*100:+.1f}%" for k, v in others)
    print(f"{name:10s} {src:6s} {bk:12s} {bv['exc']*100:+8.2f}% {bv['b2']*100:+7.2f}% {bv['pos']:3.0%} {bv['ic']:+8.4f}   [{tag}]", flush=True)
print("\n  MAX gross-alpha config per universe above. best-2>0 & pos>50% => not one-fold luck. (GROSS; tradeability annotated in G5.)")
