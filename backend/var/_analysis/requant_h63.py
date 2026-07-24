"""RE-QUANTIFY 시점23 H63 (workflow #5): H63 had net_ANN +15.1% > H21 +12.3% AND
best-2 +2.38% > +1.09% (BOTH pre-registered primary metrics favored it), yet was
rejected on an 8-fold bear −0.83% + a cross-run 'regime flip'. Test: bootstrap the
bear-tercile mean CIs for H21 and H63 — is H63 bear −0.83% distinguishable from H21
+0.35%? And bootstrap the net_ANN gap — is +15.1% vs +12.3% inside fold-count noise?
Settle whether the bear veto is statistically real before crediting it over the two
metrics that favored H63. KR production cache, ret_fwd_{21,63}d present in-cache.
Usage: uv run python var/_analysis/requant_h63.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scripts.train_lgbm import ALL_FEATURE_COLS

STEP, TRAIN_MIN, TRAIL, SUB = 42, 504, 756, 35000
SEEDS = [0, 1]; RNG = np.random.default_rng(0)
LGB = dict(n_estimators=400, num_leaves=31, learning_rate=0.03, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)

d0 = pd.read_parquet("var/_bt_period_KR_2018-01-01_2024-01-01.parquet")
d1 = pd.read_parquet("var/_bt_period_KR_2023-06-01_2026-07-01.parquet")
common = [c for c in d0.columns if c in d1.columns]
df = pd.concat([d0[common], d1[common]], ignore_index=True)
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
df = df.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last").reset_index(drop=True)
feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
dates = np.sort(df["date"].unique())


def walk(H):
    ret = f"ret_fwd_{H}d"; emb = H
    df["_mn"] = df[ret] - df.groupby("date")[ret].transform("mean")
    reb = list(range(TRAIN_MIN, len(dates) - emb, STEP))
    e, bench = [], []
    for k in reb:
        t = dates[k]; cut = dates[k - emb]; lo = dates[max(0, k - emb - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["_mn", ret]).copy()
        te = df[df["date"] == t].dropna(subset=[ret]).copy()
        if len(tr) < 4000 or len(te) < 25:
            continue
        if len(tr) > SUB:
            tr = tr.sample(SUB, random_state=k)
        g = tr.groupby("date"); tr[feats] = (tr[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
        sub = te[feats]; te[feats] = (sub - sub.mean()) / (sub.std() + 1e-9)
        sw = np.abs(tr["_mn"].values) + 1e-6; y = pd.to_numeric(te[ret], errors="coerce").values
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[feats].astype(float), tr["_mn"], sample_weight=sw).predict(te[feats].astype(float)) for s in SEEDS], axis=0)
        sel = p >= np.quantile(p, 0.9)
        e.append(float(np.nanmean(y[sel]) - np.nanmean(y))); bench.append(float(np.nanmean(y)))
    e = np.array(e); bench = np.array(bench); ppy = 252.0 / H
    net_ann = e.mean() * ppy
    ann_boot = [RNG.choice(e, len(e), replace=True).mean() * ppy for _ in range(3000)]
    be = e[bench <= np.quantile(bench, 1/3)]
    bm = [RNG.choice(be, len(be), replace=True).mean() for _ in range(3000)]
    b2 = np.mean(np.sort(e)[:-2])
    return dict(net_ann=net_ann, ann_lo=np.percentile(ann_boot, 5), ann_hi=np.percentile(ann_boot, 95),
                b2=b2, bear=be.mean(), blo=np.percentile(bm, 5), bhi=np.percentile(bm, 95), n=len(e), nb=len(be))


print(f"{'horizon':8s} {'net_ANN[90%CI]':>26s} {'best-2':>8s} {'bear[90%CI]':>24s} {'folds':>6s}")
R = {}
for H in (21, 63):
    r = walk(H); R[H] = r
    print(f"H{H:<7d} {r['net_ann']*100:+6.1f}[{r['ann_lo']*100:+.1f},{r['ann_hi']*100:+.1f}]%  {r['b2']*100:+6.2f}% "
          f"{r['bear']*100:+6.2f}[{r['blo']*100:+.2f},{r['bhi']*100:+.2f}]  {r['n']}/{r['nb']}bear", flush=True)
print(f"\n  net_ANN gap H63−H21 = {(R[63]['net_ann']-R[21]['net_ann'])*100:+.1f}%p; CIs overlap? "
      f"{'YES(구별불가)' if R[63]['ann_lo'] < R[21]['ann_hi'] and R[21]['ann_lo'] < R[63]['ann_hi'] else 'NO'}")
print(f"  H63 bear CI includes 0? {'YES' if R[63]['blo'] < 0 < R[63]['bhi'] else 'NO(음수확정)'}; "
      f"H63 bear vs H21 bear distinguishable? {'NO(구별불가)' if R[63]['bhi'] > R[21]['blo'] else 'YES'}")
print("  판정: net_ANN CI가 겹치고 H63 bear가 0 포함이면 → H63 기각(8폴드 bear)은 노이즈 기반, 두 주지표 우위를 무효화 못함.")
