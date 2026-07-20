"""Roadmap test-now experiments C2 (liquidity-slice) + B1 (regime-segmented
models), one cache load per market, judged on per-market walk-forward
top-decile EXCESS (the mirage-proof bar). Both remain honest longshots after
C4 showed the US decay is unmeasurable; run for completeness ("모두 다").

C2 — does surviving alpha CONCENTRATE in the illiquid tail? Split each date's
     cross-section into amihud_illiq_63d terciles; measure top-decile excess
     WITHIN each tercile. If the illiquid tercile carries the excess while the
     liquid one is ~0, the mega-cap universe is diluting the edge.
B1 — regime-segmented SEPARATE models (extends market-specialization inward):
     split the trailing train window by vix_pctile_252d median into calm/stress,
     fit one lgbm each, route the test date to its regime's model. Compare
     top-decile excess vs the pooled baseline; check the stress folds
     specifically (the regime-luck failure mode that killed the ensemble).

Usage: uv run python var/_analysis/wf_roadmap_exp.py KR   (or US)
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

MARKET = sys.argv[1] if len(sys.argv) > 1 else "KR"
RET = "ret_fwd_21d"
LGB = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
STEP, TRAIN_MIN, TRAIL, EMB, DEC = 63, 504, 756, 21, 0.9
ILLIQ = "amihud_illiq_63d"
REGF = "vix_pctile_252d"

df = pd.read_parquet(f"var/_bt_period_{MARKET}_2018-01-01_2024-01-01.parquet")
df["date"] = pd.to_datetime(df["date"])
feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
illiq_raw = df[ILLIQ].copy() if ILLIQ in df.columns else None
reg_raw = df[REGF].copy() if REGF in df.columns else None
g = df.groupby("date")
df[feats] = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
if illiq_raw is not None:
    df["_illiq"] = illiq_raw
if reg_raw is not None:
    df["_reg"] = reg_raw
dates = np.sort(df["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
print(f"{MARKET}: {len(df)} rows, {len(reb)} folds", flush=True)


def excess(pred, y):
    thr = np.quantile(pred, DEC); sel = pred >= thr
    return (np.nanmean(y[sel]) - np.nanmean(y)) if sel.sum() else np.nan


c2, b1 = [], []
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["mn"])
    te = df[df["date"] == t].dropna(subset=[RET])
    if len(tr) < 5000 or len(te) < 60:
        continue
    sw = np.abs(tr["mn"].values)
    y = pd.to_numeric(te[RET], errors="coerce").values
    pooled = lgb.LGBMRegressor(**LGB).fit(tr[feats].astype(float), tr["mn"].astype(float), sample_weight=sw)
    p = pooled.predict(te[feats].astype(float))

    # ── C2: top-decile excess WITHIN each illiquidity tercile ──────────────
    if "_illiq" in te.columns and te["_illiq"].notna().sum() > 45:
        il = te["_illiq"].values
        q1, q2 = np.nanquantile(il, [1/3, 2/3])
        rec = {"date": pd.Timestamp(t).date()}
        for name, mask in [("liq", il <= q1), ("mid", (il > q1) & (il <= q2)), ("illiq", il > q2), ("all", np.ones(len(il), bool))]:
            if mask.sum() >= 20:
                rec[f"exc_{name}"] = excess(p[mask], y[mask])
        c2.append(rec)

    # ── B1: regime-segmented models (calm/stress by vix_pctile) ────────────
    if "_reg" in tr.columns and tr["_reg"].notna().sum() > 1000:
        med = tr["_reg"].median()
        calm = tr[tr["_reg"] <= med]; stress = tr[tr["_reg"] > med]
        if len(calm) > 3000 and len(stress) > 3000:
            mc = lgb.LGBMRegressor(**LGB).fit(calm[feats].astype(float), calm["mn"].astype(float),
                                              sample_weight=np.abs(calm["mn"].values))
            ms = lgb.LGBMRegressor(**LGB).fit(stress[feats].astype(float), stress["mn"].astype(float),
                                              sample_weight=np.abs(stress["mn"].values))
            t_reg = te["_reg"].median() if "_reg" in te.columns else med
            routed = (ms if t_reg > med else mc).predict(te[feats].astype(float))
            b1.append({"date": pd.Timestamp(t).date(), "is_stress": bool(t_reg > med),
                       "exc_pooled": excess(p, y), "exc_routed": excess(routed, y)})

wpy = 252.0 / STEP


def stat(s):
    s = pd.Series(s).dropna()
    return f"mean={s.mean()*100:+6.2f}%  Sharpe={s.mean()/s.std()*np.sqrt(wpy) if s.std()>0 else 0:+5.2f}  >0%={(s>0).mean():4.0%}"


c2 = pd.DataFrame(c2)
print(f"\n===== C2 (liquidity-slice, {MARKET}) — top-decile excess WITHIN each illiq tercile =====")
for col in ("exc_liq", "exc_mid", "exc_illiq", "exc_all"):
    if col in c2.columns:
        print(f"  {col:10s} {stat(c2[col])}")
print("  read: if exc_illiq >> exc_liq, surviving alpha lives in illiquid names (mega-cap universe dilutes).")

b1 = pd.DataFrame(b1)
print(f"\n===== B1 (regime-segmented models, {MARKET}) — routed vs pooled =====")
if len(b1):
    print(f"  pooled  {stat(b1['exc_pooled'])}")
    print(f"  routed  {stat(b1['exc_routed'])}")
    d = b1["exc_routed"] - b1["exc_pooled"]
    print(f"  routed-pooled: mean={d.mean()*100:+.2f}%  winVsPooled={ (d>0).mean():.0%} ({int((d>0).sum())}/{len(d)})")
    st = b1[b1["is_stress"]]
    if len(st):
        ds = st["exc_routed"] - st["exc_pooled"]
        print(f"  STRESS folds ({len(st)}): routed {st['exc_routed'].mean()*100:+.2f}% vs pooled {st['exc_pooled'].mean()*100:+.2f}% "
              f"(routed-pooled {ds.mean()*100:+.2f}%, win {(ds>0).mean():.0%})")
    print("  adopt only if routed beats pooled at MEDIAN + does NOT lose stress folds (ensemble-luck guard).")
