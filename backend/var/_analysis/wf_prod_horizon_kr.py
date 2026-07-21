"""Verify the H42 (longer-horizon) finding on the FULL PRODUCTION model + universe
before touching train_production. The lean-price sweep found 42d >> 21d on
liquid-KR marcap slices; does it hold with the 478-feature production stack on
the actual production KR cache (KOSPI200+KOSDAQ150, 2018-2023)?

Production recipe: 478 features, per-date cross-section z-score, mn (market-
neutral) label, |label| sample weight, 3-seed. Compares horizons 21d (native,
baseline) / 42d (computed by joining the pykrx close panel) / 63d (native).
Judged on ANNUALIZED top-decile EXCESS (net-of-cost) + best-2 + bear so the
horizons compare fairly.

Usage: uv run python var/_analysis/wf_prod_horizon_kr.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

TRAIN_MIN, TRAIL, DEC, BPS = 504, 756, 0.9, 30   # KR round-trip ~ 2*30bps
SEEDS = [0, 1, 2]
LGB = dict(n_estimators=400, num_leaves=31, learning_rate=0.03, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)

# continuous 2018-2026 (base + gap cache) for more folds at long horizons
d0 = pd.read_parquet("var/_bt_period_KR_2018-01-01_2024-01-01.parquet")
d1 = pd.read_parquet("var/_bt_period_KR_2023-06-01_2026-07-01.parquet")
common = [c for c in d0.columns if c in d1.columns]
df = pd.concat([d0[common], d1[common]], ignore_index=True)
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str).str.zfill(6)
df = df.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last")
feats = [c for c in ALL_FEATURE_COLS if c in df.columns]

# 42d forward return from the pykrx close panel (join by date,ticker)
pk = pd.concat([pd.read_parquet(f"var/_analysis/px_KR_{b}_PYKRX.parquet") for b in ("LARGE", "MID", "MICRO")],
               ignore_index=True).drop_duplicates(["date", "ticker"]) if True else None
pk["date"] = pd.to_datetime(pk["date"]); pk["ticker"] = pk["ticker"].astype(str).str.zfill(6)
pk = pk.sort_values(["ticker", "date"])
pk["ret_fwd_42d"] = pk.groupby("ticker", group_keys=False)["close"].apply(lambda s: s.pct_change(42).shift(-42))
df = df.merge(pk[["date", "ticker", "ret_fwd_42d"]], on=["date", "ticker"], how="left")
print(f"KR prod: {len(df)} rows, {len(feats)} feats; 42d-join coverage {df['ret_fwd_42d'].notna().mean():.0%}", flush=True)

g = df.groupby("date"); df[feats] = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
dates = np.sort(df["date"].unique())

HZ = {21: "ret_fwd_21d", 42: "ret_fwd_42d", 63: "ret_fwd_63d"}


def run(h, retcol):
    step = h
    reb = list(range(TRAIN_MIN, len(dates) - h, step))
    df["_mn"] = df[retcol] - df.groupby("date")[retcol].transform("mean")
    e, turns, bench, ics = [], [], [], []; prev = None
    for k in reb:
        t = dates[k]; cut = dates[k - h]; lo = dates[max(0, k - h - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["_mn"])
        te = df[df["date"] == t].dropna(subset=[retcol])
        if len(tr) < 5000 or len(te) < 30:
            continue
        y = pd.to_numeric(te[retcol], errors="coerce").values; sw = np.abs(tr["_mn"].values); tk = te["ticker"].values
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[feats].astype(float), tr["_mn"], sample_weight=sw).predict(te[feats].astype(float)) for s in SEEDS], axis=0)
        sel = p >= np.quantile(p, DEC)
        e.append(float(np.nanmean(y[sel]) - np.nanmean(y)))
        cur = set(tk[sel]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else np.nan); prev = cur
        bench.append(float(np.nanmean(y))); ics.append(spearmanr(p, te["_mn"].values, nan_policy="omit").correlation)
    e = np.array(e); bench = np.array(bench); turn = np.nanmean(turns); ppy = 252.0 / step
    net = e.mean() - (turn if turn == turn else 0) * 2 * BPS / 1e4
    b2 = np.mean(np.sort(e)[:-2]) if len(e) > 2 else np.nan
    bear = np.nanmean(e[bench <= np.quantile(bench, 1/3)])
    return dict(ic=np.nanmean(ics), gross=e.mean(), net=net, net_ann=net * ppy, b2=b2, bear=bear, turn=turn, pos=(e > 0).mean(), n=len(e))


print(f"\n===== KR PRODUCTION MODEL (478 feats) — horizon comparison =====")
print(f"{'horizon':10s} {'folds':>5s} {'ic':>8s} {'grossExc':>9s} {'net/period':>10s} {'net_ANN':>8s} {'best-2':>8s} {'bear':>7s} {'pos':>4s}")
for h in (21, 42, 63):
    r = run(h, HZ[h])
    tag = " (base)" if h == 21 else ""
    print(f"H{h}{tag:8s} {r['n']:5d} {r['ic']:+8.4f} {r['gross']*100:+8.2f}% {r['net']*100:+9.2f}% {r['net_ann']*100:+7.1f}% "
          f"{r['b2']*100:+7.2f}% {r['bear']*100:+6.2f}% {r['pos']:3.0%}", flush=True)
print("  adopt longer horizon for production only if net_ANN + best-2 beat H21 AND bear holds.")
