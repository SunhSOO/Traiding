"""No-complacency check: does the lgbm+ridge ENSEMBLE still beat pure lgbm on
the RECENT US window actually used by production (_fs_ab_US_365.parquet,
2025-06..2026-06) — not just the 2022-23 span excess_split.py validated?

Faithfully replicates the production path: per-date cross-section z-score of
features FIRST, then lgbm on z, ridge on _prep(z) (clip±10, 0-fill), ensemble =
rank-average — exactly EnsembleRankModel. Metric = per-date top-decile EXCESS
(decile mean − universe mean = skill beyond beta), the measure that exposed
lgbm's US edge as beta tilt.

Usage: uv run python var/_analysis/excess_recent_us.py
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.linear_model import Ridge
from scripts.train_lgbm import ALL_FEATURE_COLS
from training.ensemble_model import EnsembleRankModel

TGT, RET = "rank_fwd_21d", "ret_fwd_21d"
CACHE = "var/_fs_ab_US_365.parquet"
SPLIT = "2026-03-01"   # train ≤ split, test > split (last ~3 months, labels to ~2026-05)

df = pd.read_parquet(CACHE); df["date"] = pd.to_datetime(df["date"])
feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
# production preprocessing: per-date cross-section z-score of features
g = df.groupby("date")
df[feats] = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)

tr = df[df["date"] <= SPLIT].dropna(subset=[TGT])
te = df[df["date"] > SPLIT].dropna(subset=[RET]).reset_index(drop=True)
print(f"US recent  train≤{SPLIT} n={len(tr)}  test n={len(te)} "
      f"({te['date'].min().date()}..{te['date'].max().date()}, {te['date'].nunique()} dates)", flush=True)
sw = np.abs(tr[TGT].values - 0.5)
Ztr, ytr, Zte = tr[feats].astype(float), tr[TGT].astype(float), te[feats].astype(float)

sc = {}
lgm = lgb.LGBMRegressor(n_estimators=500, learning_rate=0.03, num_leaves=31,
                        n_jobs=-1, verbose=-1).fit(Ztr, ytr, sample_weight=sw)
sc["lgbm"] = lgm.predict(Zte)
rg = Ridge(alpha=10.0).fit(EnsembleRankModel._prep(Ztr), ytr, sample_weight=sw)
sc["ridge"] = rg.predict(EnsembleRankModel._prep(Zte))
sc["ens"] = EnsembleRankModel(lgm, rg).predict(Zte)   # exact production object

te2 = te.copy(); te2["_y"] = pd.to_numeric(te2[RET], errors="coerce")
print(f"{'model':6s} {'meanExcess21d':>13s} {'ExcessSharpe':>13s} {'Excess>0%':>10s} {'meanIC':>8s}")
for name in ("lgbm", "ridge", "ens"):
    te2["s"] = sc[name]; exc = []; ics = []
    for _, gg in te2.groupby("date"):
        gg = gg.dropna(subset=["s", "_y"])
        if len(gg) < 20: continue
        top = gg[gg["s"] >= gg["s"].quantile(0.9)]["_y"]
        exc.append(top.mean() - gg["_y"].mean())
        ics.append(gg["s"].rank().corr(gg["_y"].rank()))
    e = np.array([v for v in exc if pd.notna(v)]); ic = np.array([v for v in ics if pd.notna(v)])
    esh = e.mean()/e.std()*np.sqrt(252/21) if e.std() > 0 else 0
    print(f"{name:6s} {e.mean()*100:+11.3f}% {esh:+12.2f} {(e>0).mean():9.0%} {ic.mean():+8.4f}", flush=True)
