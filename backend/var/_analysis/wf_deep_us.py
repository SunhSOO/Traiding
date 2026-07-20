"""DEEP walk-forward for the US lgbm-vs-ridge-vs-ensemble deploy decision.

One heavy pass over the 6yr cache (load ONCE — memory-smart on a single box),
recording per rebalance everything the downstream regime/cost/consistency
analyses need, so those can read a tiny CSV instead of reloading 1.4GB:

  per fold, per model {lgbm, ridge, ens}:
    ic_*        rank-IC(score, realized ret)
    exc_*       top-decile mean return  MINUS universe mean (excess = skill)
    turn_*      1 - Jaccard(top-decile set_t, set_{t-1})   (name churn ~ cost)
  per fold (regime context):
    bench       universe mean fwd return (bull/bear proxy)
    disp        cross-sectional stdev of fwd return (dispersion/opportunity)

Faithfully mirrors production: per-date cross-section z-score FIRST, lgbm on z,
ridge on _prep(z), ens = EnsembleRankModel rank-average. Output ->
var/_analysis/wf_deep_US.csv
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.linear_model import Ridge
from scripts.train_lgbm import ALL_FEATURE_COLS
from training.ensemble_model import EnsembleRankModel

MARKET = "US"
TGT, RET = "rank_fwd_21d", "ret_fwd_21d"
STEP, EMBARGO, TRAIN_MIN, TRAIL = 63, 21, 504, 756
LGB = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, n_jobs=-1, verbose=-1)
DEC = 0.9   # top decile

df = pd.read_parquet(f"var/_bt_period_{MARKET}_2018-01-01_2024-01-01.parquet")
df["date"] = pd.to_datetime(df["date"])
feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
# production preprocessing: per-date cross-section z-score of features (once)
g = df.groupby("date")
df[feats] = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
dates = np.sort(df["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - EMBARGO, STEP))
print(f"{MARKET} deep: {len(feats)} feats, {len(reb)} rebalances "
      f"({pd.Timestamp(dates[reb[0]]).date()}..{pd.Timestamp(dates[reb[-1]]).date()})", flush=True)

def topset(score, tick):
    s = pd.Series(score); thr = s.quantile(DEC)
    return set(pd.Series(tick).values[(s >= thr).values])

prev = {"lgbm": None, "ridge": None, "ens": None}
rows = []
for k in reb:
    t = dates[k]; train_cut = dates[k - EMBARGO]; lo = dates[max(0, k - EMBARGO - TRAIL)]
    tr = df[(df["date"] <= train_cut) & (df["date"] > lo)].dropna(subset=[TGT])
    te = df[df["date"] == t].dropna(subset=[RET])
    if len(tr) < 5000 or len(te) < 30:
        continue
    sw = np.abs(tr[TGT].values - 0.5)
    Ztr, ytr = tr[feats].astype(float), tr[TGT].astype(float)
    Zte = te[feats].astype(float)
    yte = pd.to_numeric(te[RET], errors="coerce").values
    tick = te["ticker"].values
    lgm = lgb.LGBMRegressor(**LGB).fit(Ztr, ytr, sample_weight=sw)
    rg = Ridge(alpha=10.0).fit(EnsembleRankModel._prep(Ztr), ytr, sample_weight=sw)
    sc = {"lgbm": lgm.predict(Zte), "ridge": rg.predict(EnsembleRankModel._prep(Zte)),
          "ens": EnsembleRankModel(lgm, rg).predict(Zte)}
    bench = float(np.nanmean(yte)); disp = float(np.nanstd(yte))
    rec = {"date": pd.Timestamp(t).date(), "n": int(len(te)), "bench": bench, "disp": disp}
    for name, s in sc.items():
        m = pd.Series(s).notna() & pd.Series(yte).notna()
        ss, yy = pd.Series(s)[m], pd.Series(yte)[m.values]
        rec[f"ic_{name}"] = float(ss.rank().corr(yy.rank())) if m.sum() >= 20 else np.nan
        thr = ss.quantile(DEC)
        rec[f"exc_{name}"] = float(yy[ss >= thr].mean() - yy.mean()) if m.sum() >= 20 else np.nan
        cur = topset(s, tick)
        if prev[name] is not None and cur and prev[name]:
            rec[f"turn_{name}"] = 1.0 - len(cur & prev[name]) / len(cur | prev[name])
        else:
            rec[f"turn_{name}"] = np.nan
        prev[name] = cur
    rows.append(rec)
    print(f"  {rec['date']}  n={rec['n']:4d} bench={bench:+.3f} "
          f"exc lgbm={rec['exc_lgbm']:+.3f} ens={rec['exc_ens']:+.3f}", flush=True)

r = pd.DataFrame(rows)
out = f"var/_analysis/wf_deep_{MARKET}.csv"
r.to_csv(out, index=False)
wpy = 252.0 / STEP
print(f"\n{MARKET} DEEP WALK-FORWARD ({len(r)} folds) -> {out}")
print(f"{'model':6s} {'meanIC':>8s} {'meanExc':>9s} {'ExcSharpe':>10s} {'Exc>0%':>8s} {'meanTurn':>9s} {'winVsLGBM':>10s}")
for name in ("lgbm", "ridge", "ens"):
    ic = r[f"ic_{name}"].dropna(); exc = r[f"exc_{name}"].dropna(); turn = r[f"turn_{name}"].dropna()
    esh = exc.mean() / exc.std() * np.sqrt(wpy) if exc.std() > 0 else 0
    win = (r[f"exc_{name}"] > r["exc_lgbm"]).mean() if name != "lgbm" else np.nan
    ws = f"{win:.0%}" if not np.isnan(win) else "  -"
    print(f"{name:6s} {ic.mean():+8.4f} {exc.mean()*100:+8.2f}% {esh:+10.2f} "
          f"{(exc>0).mean():7.0%} {turn.mean():9.2f} {ws:>10s}")
