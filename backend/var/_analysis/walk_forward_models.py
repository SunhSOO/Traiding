"""Walk-forward model comparison for a market: lgbm vs ridge vs lgbm+ridge
ensemble. At each rebalance (step 63d), retrain on trailing data (≤ t-embargo),
predict the OOS cross-section at t, record per-fold rank-IC and top-decile fwd
return. Aggregates mean IC, IC_IR, win-rate vs lgbm, and basket return — the
robust check of the single-split finding (US: ridge/ensemble beat lgbm).

Usage: uv run python var/_analysis/walk_forward_models.py US
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.linear_model import Ridge
from scripts.train_lgbm import ALL_FEATURE_COLS

MARKET = sys.argv[1] if len(sys.argv) > 1 else "US"
TGT, RET = "rank_fwd_21d", "ret_fwd_21d"
STEP, EMBARGO, TRAIN_MIN = 63, 21, 504   # rebalance/embargo/min-train (trading days)
LGB = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, n_jobs=-1, verbose=-1)

df = pd.read_parquet(f"var/_bt_period_{MARKET}_2018-01-01_2024-01-01.parquet")
df["date"] = pd.to_datetime(df["date"])
feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
dates = np.sort(df["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - EMBARGO, STEP))
print(f"{MARKET}: {len(feats)} feats, {len(reb)} rebalances "
      f"({pd.Timestamp(dates[reb[0]]).date()}..{pd.Timestamp(dates[reb[-1]]).date()})", flush=True)

def ic_dec(score, y):
    m = pd.Series(score).notna() & pd.Series(y).notna()
    if m.sum() < 20: return None, None, None
    s, yy = pd.Series(score)[m], pd.Series(y).reset_index(drop=True)[m.values]
    ic = s.rank().corr(yy.rank())
    thr = s.quantile(0.9)
    dec = pd.to_numeric(yy[s >= thr], errors="coerce").mean()
    bench = pd.to_numeric(yy, errors="coerce").mean()       # universe mean (benchmark)
    return ic, dec, bench

rows = []
for k in reb:
    t = dates[k]; train_cut = dates[k - EMBARGO]; lo = dates[max(0, k - EMBARGO - 756)]
    tr = df[(df["date"] <= train_cut) & (df["date"] > lo)].dropna(subset=[TGT])
    te = df[df["date"] == t].dropna(subset=[RET])
    if len(tr) < 5000 or len(te) < 30: continue
    sw = np.abs(tr[TGT].values - 0.5)
    Xtr, ytr = tr[feats].astype(float), tr[TGT].astype(float)
    Xte, yte = te[feats].astype(float), pd.to_numeric(te[RET], errors="coerce").values
    sc = {}
    m = lgb.LGBMRegressor(**LGB).fit(Xtr, ytr, sample_weight=sw); sc["lgbm"] = m.predict(Xte)
    # ridge: finite → median-impute → z-score(train) → clip
    Xtr_c = Xtr.replace([np.inf, -np.inf], np.nan); Xte_c = Xte.replace([np.inf, -np.inf], np.nan)
    med = Xtr_c.median(); mu = Xtr_c.fillna(med).mean(); sd = Xtr_c.fillna(med).std() + 1e-9
    Ztr = ((Xtr_c.fillna(med) - mu) / sd).clip(-10, 10).fillna(0.0)   # all-NaN col → 0 (neutral)
    Zte = ((Xte_c.fillna(med) - mu) / sd).clip(-10, 10).fillna(0.0)
    r = Ridge(alpha=10.0).fit(Ztr, ytr, sample_weight=sw); sc["ridge"] = r.predict(Zte)
    sc["ens"] = (pd.Series(sc["lgbm"]).rank().values + pd.Series(sc["ridge"]).rank().values)
    rec = {"date": pd.Timestamp(t).date()}
    for name in ("lgbm", "ridge", "ens"):
        ic, dec, bench = ic_dec(sc[name], yte)
        rec[f"ic_{name}"], rec[f"dec_{name}"], rec[f"exc_{name}"] = ic, dec, (dec - bench if dec is not None else None)
    rows.append(rec)

r = pd.DataFrame(rows)
wpy = 252.0 / STEP   # rebalances per year (annualization)
out_csv = f"var/_analysis/wf_models_{MARKET}.csv"
r.to_csv(out_csv, index=False)   # persist per-fold so a print crash never loses the run
print(f"\n{MARKET} WALK-FORWARD ({len(r)} folds) -- excess(=decile-benchmark) is the true selection value. folds -> {out_csv}")
print(f"{'model':6s} {'meanIC':>8s} {'meanExcess':>11s} {'ExcessSharpe':>13s} {'Excess>0%':>10s} {'winVsLGBM(IC)':>13s}")
for name in ("lgbm", "ridge", "ens"):
    ic = r[f"ic_{name}"].dropna(); exc = r[f"exc_{name}"].dropna()
    esh = exc.mean()/exc.std()*np.sqrt(wpy) if exc.std()>0 else 0
    win = (r[f"ic_{name}"] > r["ic_lgbm"]).mean() if name != "lgbm" else np.nan
    ws = f"{win:.0%}" if not np.isnan(win) else "  -"
    print(f"{name:6s} {ic.mean():+8.4f} {exc.mean()*100:+10.2f}% {esh:+12.2f} {(exc>0).mean():9.0%} {ws:>13s}")
