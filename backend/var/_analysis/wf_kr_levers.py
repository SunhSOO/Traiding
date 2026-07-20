"""Robust per-market re-test of the two market-split KR candidates the audit
surfaced (2026-07-10) — under the SAME walk-forward top-decile EXCESS bar that
demoted the US ensemble (single-window IC can be regime-luck).

Candidates (both rejected only for hurting US under the old cross-market frame):
  * swabs_neu : features beta-neutralized (residualize each z-feature vs z-scored
                beta_252d per date) THEN |label| weight. KR single-pass rank-IC
                0.0109->0.0158 (+45%, campaign's largest one-market lift).
  * mnmh      : multi-horizon mn label = blend of per-date-standardized 5/21/63d
                residuals. KR 0.0146->0.0185 (+27%). REQUIRES embargo >= 63d.

Baseline = KR production recipe (mn label, per-date z-score, lgbm, |label| weight).
Each variant judged on realized ret_fwd_21d top-decile EXCESS (decile mean minus
universe mean = beta-removed selection value) per walk-forward fold — NOT the
single-pass rank-IC headline. Leak control is per-variant: train_cut = t - embargo
(swabs/baseline 21d, mnmh 63d), so mnmh cannot re-introduce the 245%/yr leak.

Usage: uv run python var/_analysis/wf_kr_levers.py
Output: var/_analysis/wf_kr_levers.csv
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

MARKET = "KR"
RET = "ret_fwd_21d"
STEP, TRAIN_MIN, TRAIL = 63, 504, 756
LGB = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, n_jobs=-1, verbose=-1)
DEC = 0.9

df = pd.read_parquet(f"var/_bt_period_{MARKET}_2018-01-01_2024-01-01.parquet")
df["date"] = pd.to_datetime(df["date"])
feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
print(f"{MARKET}: {len(feats)} feats, rows {len(df)}, {df['date'].min().date()}..{df['date'].max().date()}", flush=True)

# ── targets ──────────────────────────────────────────────────────────────
df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
parts = []
for h in (5, 21, 63):
    c = f"ret_fwd_{h}d"
    if c in df.columns:
        r = df[c] - df.groupby("date")[c].transform("mean")
        parts.append(r / (r.groupby(df["date"]).transform("std") + 1e-9))
df["mnmh"] = (sum(parts) / len(parts)) if parts else np.nan
print(f"mnmh horizons present: {len(parts)}/3 (need ret_fwd_5/21/63d)", flush=True)

# ── per-date feature transforms (point-in-time safe: each date uses only its
#    own cross-section), precomputed once ──────────────────────────────────
g = df.groupby("date")
Z = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
if "beta_252d" in df.columns:   # neutral: residualize z-features vs z-scored beta per date
    b = ((df["beta_252d"] - g["beta_252d"].transform("mean"))
         / (g["beta_252d"].transform("std") + 1e-9)).fillna(0.0)
    di = df["date"]
    zb = Z.mul(b, axis=0).groupby(di).transform("sum")
    bb = (b * b).groupby(di).transform("sum") + 1e-9
    N = Z - zb.div(bb, axis=0).mul(b, axis=0)
else:
    print("!! beta_252d missing — swabs_neu falls back to plain z (no neutralization)", flush=True)
    N = Z
Zc = [c + "_z" for c in feats]; Z.columns = Zc
Nc = [c + "_n" for c in feats]; N.columns = Nc
df = pd.concat([df, Z, N], axis=1)

# variant: (name, feature_cols, target, embargo)
VARIANTS = [
    ("baseline", Zc, "mn",   21),   # KR production recipe
    ("swabs_neu", Nc, "mn",  21),   # candidate A (high)
    ("mnmh",     Zc, "mnmh", 63),   # candidate B (medium), embargo 63
]

dates = np.sort(df["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - 63, STEP))   # -63 leaves room for max embargo
print(f"{len(reb)} rebalances {pd.Timestamp(dates[reb[0]]).date()}..{pd.Timestamp(dates[reb[-1]]).date()}", flush=True)

rows = []
for k in reb:
    t = dates[k]
    te = df[df["date"] == t]
    te = te[te[RET].notna()]
    if len(te) < 30:
        continue
    yreal = pd.to_numeric(te[RET], errors="coerce").values
    mn_real = te["mn"].values
    rec = {"date": pd.Timestamp(t).date(), "n": int(len(te)), "bench": float(np.nanmean(yreal))}
    for name, fc, tgt, emb in VARIANTS:
        train_cut = dates[k - emb]; lo = dates[max(0, k - emb - TRAIL)]
        tr = df[(df["date"] <= train_cut) & (df["date"] > lo)].dropna(subset=[tgt])
        if len(tr) < 4000:
            rec[f"exc_{name}"] = np.nan; rec[f"ic_{name}"] = np.nan; continue
        sw = np.abs(tr[tgt].values)
        m = lgb.LGBMRegressor(**LGB).fit(tr[fc].astype(float), tr[tgt].astype(float), sample_weight=sw)
        p = m.predict(te[fc].astype(float))
        thr = pd.Series(p).quantile(DEC)
        top = yreal[p >= thr]
        rec[f"exc_{name}"] = float(np.nanmean(top) - np.nanmean(yreal)) if len(top) else np.nan
        rec[f"ic_{name}"] = float(spearmanr(p, mn_real, nan_policy="omit").correlation)
    rows.append(rec)
    print(f"  {rec['date']} n={rec['n']:3d} exc base={rec['exc_baseline']:+.4f} "
          f"neu={rec['exc_swabs_neu']:+.4f} mnmh={rec['exc_mnmh']:+.4f}", flush=True)

r = pd.DataFrame(rows)
out = "var/_analysis/wf_kr_levers.csv"; r.to_csv(out, index=False)
wpy = 252.0 / STEP
print(f"\n{MARKET} LEVER WALK-FORWARD ({len(r)} folds) -> {out}")
print(f"{'variant':10s} {'meanIC':>8s} {'meanExc':>9s} {'ExcSharpe':>10s} {'Exc>0%':>8s} {'winVsBase':>10s} {'dExc(bp)':>9s}")
for name, _, _, _ in VARIANTS:
    ic = r[f"ic_{name}"].dropna(); exc = r[f"exc_{name}"].dropna()
    esh = exc.mean() / exc.std() * np.sqrt(wpy) if exc.std() > 0 else 0
    win = (r[f"exc_{name}"] > r["exc_baseline"]).mean() if name != "baseline" else np.nan
    ws = f"{win:.0%}" if not np.isnan(win) else "  -"
    dexc = (exc.mean() - r["exc_baseline"].dropna().mean()) * 1e4 if name != "baseline" else 0.0
    print(f"{name:10s} {ic.mean():+8.4f} {exc.mean()*100:+8.2f}% {esh:+10.2f} "
          f"{(exc>0).mean():7.0%} {ws:>10s} {dexc:+9.1f}")
print("\nverdict rule: a candidate is adopt-worthy only if per-fold top-decile EXCESS "
      "beats baseline (dExc>0, winVsBase>50%) AND holds risk-adjusted (ExcSharpe, Exc>0%) — "
      "NOT merely higher single-pass rank-IC.")
