"""Lean model comparison: train ≤2022, test 2023 on the bt_period cache; report
per-date rank-IC vs ret_fwd_21d for lgbm / ridge / catboost / lgbm+ridge ensemble.
Single split (fast), avoids the hanging xgb/full-CV compare_models."""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.linear_model import Ridge
from sklearn.impute import SimpleImputer
from scripts.train_lgbm import ALL_FEATURE_COLS

TGT, RET = "rank_fwd_21d", "ret_fwd_21d"

def ic(scores, te):
    d = pd.DataFrame({"date": te["date"].values, "s": np.asarray(scores),
                      "y": pd.to_numeric(te[RET], errors="coerce").values})
    ics = []
    for _, g in d.groupby("date"):
        m = g["s"].notna() & g["y"].notna()
        if m.sum() >= 20:
            v = g.loc[m, "s"].rank().corr(g.loc[m, "y"].rank())
            if pd.notna(v): ics.append(v)
    a = np.array(ics)
    return (a.mean(), a.mean()/a.std() if a.std()>0 else 0, len(a)) if len(a) else (np.nan, 0, 0)

for market in ("KR", "US"):
    df = pd.read_parquet(f"var/_bt_period_{market}_2018-01-01_2024-01-01.parquet")
    df["date"] = pd.to_datetime(df["date"])
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    tr = df[df["date"] <= "2022-06-01"].dropna(subset=[TGT])
    te = df[(df["date"] > "2022-06-01") & (df["date"] <= "2023-06-01")].dropna(subset=[RET]).reset_index(drop=True)
    pass  # (removed)
    sw = np.abs(tr[TGT].values - 0.5)
    Xtr, ytr, Xte = tr[feats].astype(float), tr[TGT].astype(float), te[feats].astype(float)
    print(f"\n===== {market}  train≤2022-06 → test 2022-06..2023-06 (n_te={len(te)}) =====", flush=True)
    scores = {}
    # lgbm
    m = lgb.LGBMRegressor(n_estimators=500, learning_rate=0.03, num_leaves=31, n_jobs=-1, verbose=-1)
    m.fit(Xtr, ytr, sample_weight=sw); scores["lgbm"] = m.predict(Xte)
    # ridge (needs finite + imputation + scaling)
    Xtr_c = Xtr.replace([np.inf, -np.inf], np.nan)
    Xte_c = Xte.replace([np.inf, -np.inf], np.nan)
    imp = SimpleImputer(strategy="median")
    Xtr_i, Xte_i = imp.fit_transform(Xtr_c), imp.transform(Xte_c)
    mu, sd = Xtr_i.mean(0), Xtr_i.std(0) + 1e-9
    Xtr_z = np.clip((Xtr_i - mu) / sd, -10, 10)
    Xte_z = np.clip((Xte_i - mu) / sd, -10, 10)
    r = Ridge(alpha=10.0).fit(Xtr_z, ytr, sample_weight=sw); scores["ridge"] = r.predict(Xte_z)
    # catboost (limited to avoid hang)
    try:
        from catboost import CatBoostRegressor
        cb = CatBoostRegressor(iterations=250, depth=6, learning_rate=0.05, verbose=0, thread_count=4)
        cbtr = Xtr.replace([np.inf, -np.inf], np.nan).fillna(-999)
        cbte = Xte.replace([np.inf, -np.inf], np.nan).fillna(-999)
        cb.fit(cbtr, ytr, sample_weight=sw); scores["catboost"] = cb.predict(cbte)
    except Exception as e:
        print(f"  catboost skipped: {str(e)[:50]}", flush=True)
    # ensemble lgbm+ridge (rank-average)
    if "ridge" in scores:
        scores["ens_lgbm+ridge"] = (pd.Series(scores["lgbm"]).rank() + pd.Series(scores["ridge"]).rank()).values
    for name, sc in scores.items():
        mic, ir, nd = ic(sc, te)
        print(f"  {name:16s} rank-IC={mic:+.4f}  IC_IR={ir:+.3f}  ndays={nd}", flush=True)
