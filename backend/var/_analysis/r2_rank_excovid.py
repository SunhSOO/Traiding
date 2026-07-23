"""R2 — was the rank-label rejection a COVID artifact? In the recipe re-derivation,
`no-norm +rank −blitz` lost base on H1 (2018-2022) → rejected. But H1 contains the
2020 COVID crash. This re-runs base vs no-norm−blitz vs no-norm+rank−blitz and reports
H1 net BOTH with and without the COVID window (2020-02..2020-07) folds excluded. If
rank's H1 flips positive ex-COVID, the split-half rejection was one-regime-driven.
(Even so, adopting a label that needs a regime excised is unsafe live — this is a
diligence check on the REJECTION, per "verify before excluding".)

Lean: step63, 40k subsample, 3-seed. Usage: uv run python var/_analysis/r2_rank_excovid.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scripts.train_lgbm import ALL_FEATURE_COLS

STEP, TRAIN_MIN, TRAIL, EMB, DEC, SUB = 63, 504, 756, 21, 0.9, 40000
SEEDS = [0, 1, 2]
LGB = dict(n_estimators=400, num_leaves=31, learning_rate=0.03, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
RET = "ret_fwd_21d"
COVID = (pd.Timestamp("2020-02-01"), pd.Timestamp("2020-07-31"))

d0 = pd.read_parquet("var/_bt_period_KR_2018-01-01_2024-01-01.parquet")
d1 = pd.read_parquet("var/_bt_period_KR_2023-06-01_2026-07-01.parquet")
common = [c for c in d0.columns if c in d1.columns]
df = pd.concat([d0[common], d1[common]], ignore_index=True)
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str).str.zfill(6)
df = df.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last").reset_index(drop=True)
feats_all = [c for c in ALL_FEATURE_COLS if c in df.columns]
feats_nb = [c for c in feats_all if "blitz" not in c.lower()]
df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
dates = np.sort(df["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
mid = reb[len(reb) // 2]
print(f"R2 rank ex-COVID: {len(df)} rows, {len(reb)} folds, COVID={COVID[0].date()}..{COVID[1].date()}", flush=True)


def walk(label="mn", norm=True, feats=None):
    feats = feats or feats_all
    lab = {"mn": "mn", "rank": "rank_fwd_21d"}[label]
    rows = []
    for k in reb:
        t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=[lab, RET]).copy()
        te = df[df["date"] == t].dropna(subset=[RET]).copy()
        if len(tr) < 5000 or len(te) < 30:
            continue
        if len(tr) > SUB:
            tr = tr.sample(SUB, random_state=k).sort_values("date")
        if norm:
            g = tr.groupby("date"); tr[feats] = (tr[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
            sub = te[feats]; te[feats] = (sub - sub.mean()) / (sub.std() + 1e-9)
        sw = np.abs(tr[lab].values); y = pd.to_numeric(te[RET], errors="coerce").values
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[feats].astype(float), tr[lab], sample_weight=sw).predict(te[feats].astype(float)) for s in SEEDS], axis=0)
        sel = p >= np.quantile(p, DEC)
        rows.append((k, t, float(np.nanmean(y[sel]) - np.nanmean(y))))
    return rows


VARS = [("base", dict()), ("no-norm −blitz", dict(norm=False, feats=feats_nb)),
        ("no-norm +rank −blitz", dict(norm=False, label="rank", feats=feats_nb))]
print(f"\n{'recipe':22s} {'H1_all':>8s} {'H1_exCOVID':>11s} {'H2':>8s} {'full':>8s}   (net, gross top-decile excess)")
for name, kw in VARS:
    r = walk(**kw)
    h1 = [e for k, t, e in r if k < mid]
    h1x = [e for k, t, e in r if k < mid and not (COVID[0] <= t <= COVID[1])]
    h2 = [e for k, t, e in r if k >= mid]
    full = [e for k, t, e in r if True]
    print(f"{name:22s} {np.mean(h1)*100:+7.2f}% {np.mean(h1x)*100:+10.2f}% {np.mean(h2)*100:+7.2f}% {np.mean(full)*100:+7.2f}%", flush=True)
print("  read: if rank's H1_exCOVID flips >0 and > base, the split-half rejection was COVID-driven (but excising a regime is unsafe live).")
