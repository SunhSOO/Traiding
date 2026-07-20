"""Re-validation of B1 (regime-segmented KR models) BEFORE believing it — the
single-seed run looked strong (routed +2.10% vs pooled +1.18%, stress folds
5/5) but that is the EXACT profile KR EWMA had before 3-seed re-validation
dissolved it. Apply the same bar: 3 seeds, cross-seed agreement, and a
stress-fold concentration check (the stress win is only n=5 — was it all folds
or one lucky fold?).

Split the trailing train window by vix_pctile_252d median into calm/stress, fit
one lgbm each, route the test date to its regime's model; compare to the pooled
model on top-decile EXCESS. ADOPT only if routed beats pooled across ALL seeds,
wins the majority of folds, AND the stress-fold advantage is spread (not 1 fold).

Usage: uv run python var/_analysis/wf_b1_kr_robust.py
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from scripts.train_lgbm import ALL_FEATURE_COLS

RET, REGF = "ret_fwd_21d", "vix_pctile_252d"
LGB = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
STEP, TRAIN_MIN, TRAIL, EMB, DEC = 63, 504, 756, 21, 0.9
SEEDS = [0, 1, 2]

df = pd.read_parquet("var/_bt_period_KR_2018-01-01_2024-01-01.parquet")
df["date"] = pd.to_datetime(df["date"])
feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
reg_raw = df[REGF].copy()
g = df.groupby("date")
df[feats] = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
df["_reg"] = reg_raw
dates = np.sort(df["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
print(f"KR: {len(df)} rows, {len(reb)} folds, seeds {SEEDS}", flush=True)


def exc(p, y):
    sel = p >= np.quantile(p, DEC)
    return (np.nanmean(y[sel]) - np.nanmean(y)) if sel.sum() else np.nan


rows = {s: [] for s in SEEDS}
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["mn"])
    te = df[df["date"] == t].dropna(subset=[RET])
    if len(tr) < 5000 or len(te) < 30 or tr["_reg"].notna().sum() < 1000:
        continue
    y = pd.to_numeric(te[RET], errors="coerce").values
    med = tr["_reg"].median()
    calm = tr[tr["_reg"] <= med]; stress = tr[tr["_reg"] > med]
    if len(calm) < 3000 or len(stress) < 3000:
        continue
    t_stress = bool((te["_reg"].median() if te["_reg"].notna().any() else med) > med)
    for s in SEEDS:
        pool = lgb.LGBMRegressor(**LGB, random_state=s).fit(
            tr[feats].astype(float), tr["mn"].astype(float), sample_weight=np.abs(tr["mn"].values))
        mc = lgb.LGBMRegressor(**LGB, random_state=s).fit(
            calm[feats].astype(float), calm["mn"].astype(float), sample_weight=np.abs(calm["mn"].values))
        ms = lgb.LGBMRegressor(**LGB, random_state=s).fit(
            stress[feats].astype(float), stress["mn"].astype(float), sample_weight=np.abs(stress["mn"].values))
        ep = exc(pool.predict(te[feats].astype(float)), y)
        er = exc((ms if t_stress else mc).predict(te[feats].astype(float)), y)
        rows[s].append({"date": pd.Timestamp(t).date(), "is_stress": t_stress, "pool": ep, "routed": er})

wpy = 252.0 / STEP
print(f"\n{'seed':5s} {'pooled':>8s} {'routed':>8s} {'r-p':>7s} {'winVsPool':>9s} {'strN':>4s} {'str r-p':>8s} {'strWin':>7s}")
per_seed_rp, per_seed_str = [], []
for s in SEEDS:
    r = pd.DataFrame(rows[s]); d = r["routed"] - r["pool"]
    st = r[r["is_stress"]]; ds = st["routed"] - st["pool"]
    per_seed_rp.append(d.mean()); per_seed_str.append(ds.mean())
    print(f"{s:<5d} {r['pool'].mean()*100:+7.2f}% {r['routed'].mean()*100:+7.2f}% {d.mean()*100:+6.2f}% "
          f"{(d>0).mean():9.0%} {len(st):4d} {ds.mean()*100:+7.2f}% {(ds>0).mean():7.0%}", flush=True)
print(f"\ncross-seed: routed-pooled all-positive? {all(x>0 for x in per_seed_rp)} "
      f"(seeds {[f'{x*100:+.2f}%' for x in per_seed_rp]}); stress adv all-positive? {all(x>0 for x in per_seed_str)}")

# stress-fold concentration: routed-pooled per stress fold, averaged over seeds
allr = pd.concat([pd.DataFrame(rows[s]).assign(seed=s) for s in SEEDS])
st = allr[allr["is_stress"]].copy(); st["rp"] = st["routed"] - st["pool"]
byfold = st.groupby("date")["rp"].mean()
print(f"\nstress-fold routed-pooled (avg over seeds) — concentration check:")
print(byfold.round(4).to_string())
print(f"stress folds where routed>pooled: {(byfold>0).sum()}/{len(byfold)}; "
      f"mean {byfold.mean()*100:+.2f}%, excl best {byfold.sort_values()[:-1].mean()*100:+.2f}%")
print("\nADOPT if: routed-pooled positive across ALL seeds, winVsPool>50%, and the stress advantage "
      "is spread across stress folds (not one) — else it is regime-luck like the ensemble.")
