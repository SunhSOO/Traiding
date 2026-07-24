"""RE-QUANTIFY 시점6 mn_cat (workflow #2, purest bias specimen): CatBoost had the
HIGHEST OOS IC of any challenger (US 0.0297 > norm 0.0254) but was killed on a bare
'KR 분산 ±14.04' with the KR MEAN never recorded. Instead of the old ambiguous variance
metric, apply the CORRECT metric — top-decile excess NET + rank-IC + bear-with-CI, BOTH
markets — to cat vs the lgbm baseline. Settles: was cat mis-killed, or does it fail the
right metric? (Same 478-feat continuous cache, mn label, per-date norm, all features.)
Usage: uv run python var/_analysis/mncat_requant.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from catboost import CatBoostRegressor
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

STEP, TRAIN_MIN, TRAIL, EMB, DEC, BPS, SUB = 63, 504, 756, 21, 0.9, 30, 30000
SEEDS = [0, 1]
RNG = np.random.default_rng(0)
RET = "ret_fwd_21d"
LGB = dict(n_estimators=400, num_leaves=31, learning_rate=0.03, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
CAT = dict(iterations=300, depth=5, learning_rate=0.04, loss_function="RMSE",
           l2_leaf_reg=5.0, random_strength=1.0, verbose=0, allow_writing_files=False)


def load(mkt):
    d0 = pd.read_parquet(f"var/_bt_period_{mkt}_2018-01-01_2024-01-01.parquet")
    d = d0.copy(); d["date"] = pd.to_datetime(d["date"]); d["ticker"] = d["ticker"].astype(str)
    d = d.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last").reset_index(drop=True)
    d["mn"] = d[RET] - d.groupby("date")[RET].transform("mean")
    return d


def walk(df, model):
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    dates = np.sort(df["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
    e, bench, ics = [], [], []
    for k in reb:
        t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["mn", RET]).copy()
        te = df[df["date"] == t].dropna(subset=[RET]).copy()
        if len(tr) < 4000 or len(te) < 25:
            continue
        if len(tr) > SUB:
            tr = tr.sample(SUB, random_state=k)
        gtr = tr.groupby("date"); tr[feats] = (tr[feats] - gtr[feats].transform("mean")) / (gtr[feats].transform("std") + 1e-9)
        sub = te[feats]; te[feats] = (sub - sub.mean()) / (sub.std() + 1e-9)
        sw = np.abs(tr["mn"].values); y = pd.to_numeric(te[RET], errors="coerce").values
        ps = []
        for s in SEEDS:
            if model == "lgbm":
                m = lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[feats].astype(float), tr["mn"], sample_weight=sw)
            else:
                m = CatBoostRegressor(**CAT, random_seed=s).fit(tr[feats].astype(float).values, tr["mn"].values, sample_weight=sw)
            ps.append(m.predict(te[feats].astype(float).values))
        p = np.mean(ps, axis=0); sel = p >= np.quantile(p, DEC)
        e.append(float(np.nanmean(y[sel]) - np.nanmean(y))); bench.append(float(np.nanmean(y)))
        ics.append(spearmanr(p, te["mn"].values, nan_policy="omit").correlation)
    e = np.array(e); bench = np.array(bench)
    b2 = np.mean(np.sort(e)[:-2]); be = e[bench <= np.quantile(bench, 1/3)]
    bmeans = [RNG.choice(be, len(be), replace=True).mean() for _ in range(3000)] if len(be) > 2 else [np.nan]
    return dict(ic=np.nanmean(ics), exc=e.mean(), std=e.std(), net=e.mean() - BPS / 1e4,
                b2=b2, bear=be.mean() if len(be) else np.nan,
                bear_lo=np.percentile(bmeans, 5), bear_hi=np.percentile(bmeans, 95),
                mean_lo=e.mean() - e.std(), mean_hi=e.mean() + e.std(), n=len(e))


print(f"{'mkt':4s} {'model':8s} {'rankIC':>8s} {'exc':>7s} {'mean±std(0포함?)':>20s} {'best-2':>8s} {'bear[CI]':>22s}")
for mkt in ("US", "KR"):
    df = load(mkt)
    for model in ("lgbm", "cat"):
        r = walk(df, model)
        cross0 = "0포함" if (r["mean_lo"] < 0 < r["mean_hi"]) else "0제외(+)"
        print(f"{mkt:4s} {model:8s} {r['ic']:+8.4f} {r['exc']*100:+6.2f}% "
              f"{r['exc']*100:+.2f}±{r['std']*100:.2f}({cross0}) {r['b2']*100:+7.2f}% "
              f"{r['bear']*100:+6.2f}[{r['bear_lo']*100:+.2f},{r['bear_hi']*100:+.2f}]", flush=True)
print("\n  판정: cat이 US IC·excess로 lgbm 이기고 KR mean±std가 0 제외(양수 확정)면 → 시점6 기각은 오기각(mean 은폐).")
print("        cat KR mean±std가 0 포함(앙상블 −5.1~+29.3처럼)이면 → 기각 정당(단 당시 mean을 보였어야).")
