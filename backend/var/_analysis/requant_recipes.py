"""RE-QUANTIFY the workflow-flagged recipe rejections (시점6/12) on the CORRECT metric —
top-decile excess NET + rank-IC + best-2 + conc5 + bear-with-CI, BOTH markets — vs the
incumbent baseline. Settles whether each was killed for being actually-worse, or on
unquantified variance/conc5/cross-market criteria.
  base     = mn label, per-date z, sw=|label|  (incumbent recipe)
  uniqabs  = base but sw=(1/concurrency)×|label|   (시점12, killed on unquantified conc5)
  winsor   = base but per-date z then clip ±3      (시점6, killed KR sign-flip; won US)
  rank     = base but label=rank_fwd_21d           (시점6, killed as 'tilt' on 0.0021 IC gap)
478-feat continuous 2018-2024 cache. LIMITATION (as with mn_cat): window-drift means the
original 시점6/12 numbers can't be perfectly reproduced; the cat-vs-base comparison on the
SAME clean window is the valid test.
Usage: uv run python var/_analysis/requant_recipes.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

STEP, TRAIN_MIN, TRAIL, EMB, DEC, BPS, SUB = 63, 504, 756, 21, 0.9, 30, 35000
SEEDS = [0, 1]; RNG = np.random.default_rng(0); RET = "ret_fwd_21d"
LGB = dict(n_estimators=400, num_leaves=31, learning_rate=0.03, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)


def load(mkt):
    d = pd.read_parquet(f"var/_bt_period_{mkt}_2018-01-01_2024-01-01.parquet")
    d["date"] = pd.to_datetime(d["date"]); d["ticker"] = d["ticker"].astype(str)
    d = d.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last").reset_index(drop=True)
    d["mn"] = d[RET] - d.groupby("date")[RET].transform("mean")
    return d


def walk(df, variant):
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    lab = "rank_fwd_21d" if variant == "rank" else "mn"
    dates = np.sort(df["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
    e, bench, ics = [], [], []
    for k in reb:
        t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=[lab, RET]).copy()
        te = df[df["date"] == t].dropna(subset=[RET]).copy()
        if len(tr) < 4000 or len(te) < 25:
            continue
        if len(tr) > SUB:
            tr = tr.sample(SUB, random_state=k)
        gtr = tr.groupby("date"); tr[feats] = (tr[feats] - gtr[feats].transform("mean")) / (gtr[feats].transform("std") + 1e-9)
        sub = te[feats]; te[feats] = (sub - sub.mean()) / (sub.std() + 1e-9)
        if variant == "winsor":
            tr[feats] = tr[feats].clip(-3, 3); te[feats] = te[feats].clip(-3, 3)
        yv = tr[lab].values
        if variant == "uniqabs":
            ud = np.sort(tr["date"].unique()); di = tr["date"].map(pd.Series(np.arange(len(ud)), index=ud)).values
            conc = np.clip(np.minimum(di + 1, 21), 1, None); sw = (1.0 / conc) * (np.abs(tr["mn"].values) + 1e-6)
        else:
            sw = np.abs(tr["mn"].values) + 1e-6
        y = pd.to_numeric(te[RET], errors="coerce").values
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[feats].astype(float), yv, sample_weight=sw).predict(te[feats].astype(float)) for s in SEEDS], axis=0)
        sel = p >= np.quantile(p, DEC)
        e.append(float(np.nanmean(y[sel]) - np.nanmean(y))); bench.append(float(np.nanmean(y)))
        ics.append(spearmanr(p, te["mn"].values, nan_policy="omit").correlation)
    e = np.array(e); bench = np.array(bench)
    b2 = np.mean(np.sort(e)[:-2]); pos = e[e > 0].sum(); conc5 = np.sort(e)[::-1][:5].clip(min=0).sum() / pos if pos > 0 else np.nan
    be = e[bench <= np.quantile(bench, 1/3)]
    bm = [RNG.choice(be, len(be), replace=True).mean() for _ in range(3000)] if len(be) > 2 else [np.nan]
    return dict(ic=np.nanmean(ics), net=e.mean() - BPS / 1e4, b2=b2, conc5=conc5,
                bear=be.mean() if len(be) else np.nan, blo=np.percentile(bm, 5), bhi=np.percentile(bm, 95))


print(f"{'mkt':4s} {'variant':9s} {'rankIC':>8s} {'netExc':>8s} {'best-2':>8s} {'conc5':>6s} {'bear[95%CI]':>24s}  vs base")
for mkt in ("US", "KR"):
    df = load(mkt); base = None
    for v in ("base", "uniqabs", "winsor", "rank"):
        r = walk(df, v)
        if v == "base":
            base = r
        dv = "" if v == "base" else f"  net {(r['net']-base['net'])*100:+.2f} / IC {(r['ic']-base['ic']):+.4f}"
        print(f"{mkt:4s} {v:9s} {r['ic']:+8.4f} {r['net']*100:+7.2f}% {r['b2']*100:+7.2f}% "
              f"{(r['conc5'] if r['conc5']==r['conc5'] else 0):5.0%} {r['bear']*100:+6.2f}[{r['blo']*100:+.2f},{r['bhi']*100:+.2f}]{dv}", flush=True)
    print()
print("  판정: variant가 base를 net·IC로 이기거나 대등하면 → 기각은 '더 나빠서'가 아님(편향 확인). conc5가 base와 비슷하면 conc5 기각근거 무효.")
