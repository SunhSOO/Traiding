"""RE-AUDIT CATEGORY E — the model-class levers rejected in 시점 0-14 (ridge,
ExtraTrees, LTR/ranker, lgbm+ridge ensemble), re-tested on the continuous KR
production cache (478 feats, 2018-2026) with the SAME battery (3-seed, best-2,
conc5, bear). They were rejected on the OLD harness; do they stay rejected on
the clean full-battery test? Base = lgbm (the incumbent learner).

Usage: uv run python var/_analysis/wf_rejections_kr.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.linear_model import Ridge
from sklearn.ensemble import ExtraTreesRegressor
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

FULL = len(sys.argv) > 1 and sys.argv[1] == "full"   # `full` = heavy fidelity (step21, 3seed, ET200, no subsample)
STEP, TRAIN_MIN, TRAIL, EMB, DEC, BPS = (21 if FULL else 63), 504, 756, 21, 0.9, 30
SEEDS = [0, 1, 2] if FULL else [0, 1]
SUBSAMPLE = None if FULL else 40000
ET = dict(n_estimators=200, min_samples_leaf=50) if FULL else dict(n_estimators=80, min_samples_leaf=100, max_features=0.3)
LGB = dict(n_estimators=400, num_leaves=31, learning_rate=0.03, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
RET = "ret_fwd_21d"

d0 = pd.read_parquet("var/_bt_period_KR_2018-01-01_2024-01-01.parquet")
d1 = pd.read_parquet("var/_bt_period_KR_2023-06-01_2026-07-01.parquet")
common = [c for c in d0.columns if c in d1.columns]
df = pd.concat([d0[common], d1[common]], ignore_index=True)
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str).str.zfill(6)
df = df.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last").reset_index(drop=True)
feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
df["rk"] = df.groupby("date")["mn"].rank(pct=True)
print(f"KR rejections re-audit [{'FULL' if FULL else 'light'}]: {len(df)} rows, {len(feats)} feats, "
      f"step={STEP} seeds={SEEDS} subsample={SUBSAMPLE} ET={ET['n_estimators']}t (per-fold norm)", flush=True)
dates = np.sort(df["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
def prep(X): return pd.DataFrame(X).replace([np.inf, -np.inf], np.nan).clip(-10, 10).fillna(0).to_numpy(float)


def walk(model):
    e, turns, bench, ics = [], [], [], []; prev = None
    for k in reb:
        t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["mn"]).copy()
        te = df[df["date"] == t].dropna(subset=[RET]).copy()
        if len(tr) < 5000 or len(te) < 30:
            continue
        if SUBSAMPLE and len(tr) > SUBSAMPLE:
            tr = tr.sample(SUBSAMPLE, random_state=k).sort_values("date")
        gtr = tr.groupby("date"); tr[feats] = (tr[feats] - gtr[feats].transform("mean")) / (gtr[feats].transform("std") + 1e-9)
        sub = te[feats]; te[feats] = (sub - sub.mean()) / (sub.std() + 1e-9)
        y = pd.to_numeric(te[RET], errors="coerce").values; sw = np.abs(tr["mn"].values); tk = te["ticker"].values
        X, Xt = tr[feats].astype(float), te[feats].astype(float)
        ps = []
        for s in SEEDS:
            if model == "lgbm":
                ps.append(lgb.LGBMRegressor(**LGB, random_state=s).fit(X, tr["mn"], sample_weight=sw).predict(Xt))
            elif model == "ridge":
                ps.append(Ridge(alpha=10).fit(prep(X), tr["mn"], sample_weight=sw).predict(prep(Xt)))
            elif model == "extratrees":
                ps.append(ExtraTreesRegressor(**ET, n_jobs=-1, random_state=s).fit(prep(X), tr["mn"], sample_weight=sw).predict(prep(Xt)))
            elif model == "ltr":
                rnk = (tr["rk"].values * 9.999).astype(int)
                grp = tr.groupby("date").size().values
                tr2 = tr.sort_values("date")
                m = lgb.LGBMRanker(n_estimators=400, num_leaves=31, learning_rate=0.03, n_jobs=-1, verbose=-1, random_state=s)
                m.fit(tr2[feats].astype(float), (tr2["rk"].values * 9.999).astype(int), group=tr2.groupby("date").size().values)
                ps.append(m.predict(Xt))
            elif model == "ens_ridge":
                lg = lgb.LGBMRegressor(**LGB, random_state=s).fit(X, tr["mn"], sample_weight=sw).predict(Xt)
                rg = Ridge(alpha=10).fit(prep(X), tr["mn"], sample_weight=sw).predict(prep(Xt))
                ps.append(pd.Series(lg).rank().values + pd.Series(rg).rank().values)
        p = np.mean(ps, axis=0)
        s_ = p >= np.quantile(p, DEC)
        e.append(float(np.nanmean(y[s_]) - np.nanmean(y)))
        cur = set(tk[s_]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else np.nan); prev = cur
        bench.append(float(np.nanmean(y))); ics.append(spearmanr(p, te["mn"].values, nan_policy="omit").correlation)
    e = np.array(e); bench = np.array(bench); turn = np.nanmean(turns)
    net = e.mean() - (turn if turn == turn else 0) * 2 * BPS / 1e4
    b2 = np.mean(np.sort(e)[:-2]); pos = e[e > 0].sum(); conc5 = np.sort(e)[::-1][:5].clip(min=0).sum() / pos if pos > 0 else np.nan
    bear = np.nanmean(e[bench <= np.quantile(bench, 1/3)])
    return dict(ic=np.nanmean(ics), net=net, b2=b2, conc5=conc5, bear=bear, pos=(e > 0).mean())


print(f"\n===== KR REJECTED-MODEL RE-AUDIT (3-seed, {BPS}bps) — do they stay rejected? =====")
print(f"{'model':12s} {'ic':>8s} {'net':>7s} {'best-2':>8s} {'conc5':>6s} {'bear':>7s} {'pos':>4s} {'vsLGBM':>8s}")
base = None
for m in ("lgbm", "ridge", "extratrees", "ltr", "ens_ridge"):
    r = walk(m)
    if m == "lgbm":
        base = r
    dv = "" if m == "lgbm" else f"  {(r['net']-base['net'])*100:+.2f}"
    print(f"{m:12s} {r['ic']:+8.4f} {r['net']*100:+6.2f}% {r['b2']*100:+7.2f}% {r['conc5']:5.0%} {r['bear']*100:+6.2f}% {r['pos']:3.0%}{dv:>8s}", flush=True)
print("  stays rejected if it does not beat lgbm on net AND best-2/bear.")
