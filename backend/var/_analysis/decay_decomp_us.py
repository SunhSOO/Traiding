"""C4 — decompose the US 2025-26 selection decay: is it a REMOVABLE factor/beta
tilt (fixable by a neutralized basket) or GENUINELY lost cross-sectional skill?

Long-only top-decile excess can decay while true long/short skill survives (the
decay is then a factor tilt the long-only basket rides). So we add:
  exc_top    = top-decile mean  - universe mean   (what we currently trade)
  exc_bottom = universe mean    - bottom-decile mean   (short-side skill)
  spread     = exc_top + exc_bottom   (market-neutral long/short skill; strips
               the residual factor/beta tilt that long-only still rides)

Two windows, SAME production-recipe lgbm:
  PRE-2025  = walk-forward on the 6yr cache (2018-2023) -> the healthy baseline
  2025-26   = model trained on ALL 6yr, scored on the 365 live cache (the decay
              window, 2025-06..2026-06). This is the actual live scenario
              (train on the past, apply forward across the 2024-25 gap).

Mirage-proof: this decomposes REALIZED returns, so it cannot fool us on rank-IC.
Verdict gate: if the SPREAD survives 2025-26 while exc_top decays -> the decay is
a removable tilt, build a neutralized basket (B1/D1/C2 worth it). If the spread
ALSO collapses -> skill is genuinely gone, stop funding decay-targeted models.

Usage: uv run python var/_analysis/decay_decomp_us.py
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

RET, TGT = "ret_fwd_21d", "rank_fwd_21d"
LGB = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
TOPK, DEC = 50, 0.1
STEP, TRAIN_MIN, TRAIL, EMB = 63, 504, 756, 21


def zscore(df, feats):
    g = df.groupby("date")
    return (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)


def decomp(pred, yreal):
    """top/bottom decile excess + spread on realized returns for one cross-section."""
    m = pd.Series(pred).notna() & pd.Series(yreal).notna()
    if m.sum() < 30:
        return None
    p = pd.Series(pred)[m].values; y = pd.Series(yreal)[m].values
    hi = np.quantile(p, 1 - DEC); lo = np.quantile(p, DEC)
    um = y.mean()
    exc_top = y[p >= hi].mean() - um
    exc_bot = um - y[p <= lo].mean()
    return exc_top, exc_bot, exc_top + exc_bot


df6 = pd.read_parquet("var/_bt_period_US_2018-01-01_2024-01-01.parquet")
df6["date"] = pd.to_datetime(df6["date"])
feats = [c for c in ALL_FEATURE_COLS if c in df6.columns]
df6["mn"] = df6[RET] - df6.groupby("date")[RET].transform("mean")
df6[feats] = zscore(df6, feats)
print(f"6yr US: {len(df6)} rows, {len(feats)} feats, {df6['date'].min().date()}..{df6['date'].max().date()}", flush=True)

# ── PRE-2025 baseline: walk-forward on 6yr, decompose per fold ──────────────
dates = np.sort(df6["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
pre = []
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = df6[(df6["date"] <= cut) & (df6["date"] > lo)].dropna(subset=[TGT])
    te = df6[df6["date"] == t].dropna(subset=[RET])
    if len(tr) < 5000 or len(te) < 30:
        continue
    sw = np.abs(tr[TGT].values - 0.5)
    m = lgb.LGBMRegressor(**LGB).fit(tr[feats].astype(float), tr[TGT].astype(float), sample_weight=sw)
    p = m.predict(te[feats].astype(float)); y = pd.to_numeric(te[RET], errors="coerce").values
    d = decomp(p, y)
    if d:
        ic = spearmanr(p, te["mn"].values, nan_policy="omit").correlation
        pre.append({"date": pd.Timestamp(t).date(), "exc_top": d[0], "exc_bot": d[1], "spread": d[2], "ic": ic})
pre = pd.DataFrame(pre)
print(f"pre-2025 walk-forward: {len(pre)} folds", flush=True)

# ── 2025-26 decay window: train on ALL 6yr, score the 365 live cache ────────
sel = lgb.LGBMRegressor(**LGB).fit(df6[feats].astype(float), df6[TGT].astype(float),
                                   sample_weight=np.abs(df6[TGT].values - 0.5))
top = pd.Series(sel.feature_importances_, index=feats).sort_values(ascending=False).head(TOPK).index.tolist()
model = lgb.LGBMRegressor(**LGB).fit(df6[top].astype(float), df6[TGT].astype(float),
                                     sample_weight=np.abs(df6[TGT].values - 0.5))

df365 = pd.read_parquet("var/_fs_ab_US_365.parquet")
df365["date"] = pd.to_datetime(df365["date"])
f365 = [c for c in top if c in df365.columns]
missing = [c for c in top if c not in df365.columns]
if missing:
    print(f"!! {len(missing)} top features missing in 365 cache: {missing[:5]}...", flush=True)
df365["mn"] = df365[RET] - df365.groupby("date")[RET].transform("mean")
allf = [c for c in ALL_FEATURE_COLS if c in df365.columns]
df365[allf] = zscore(df365, allf)
post = []
for t, g in df365.dropna(subset=[RET]).groupby("date"):
    if len(g) < 30:
        continue
    p = model.predict(g[f365].astype(float)); y = pd.to_numeric(g[RET], errors="coerce").values
    d = decomp(p, y)
    if d:
        ic = spearmanr(p, g["mn"].values, nan_policy="omit").correlation
        post.append({"date": pd.Timestamp(t).date(), "exc_top": d[0], "exc_bot": d[1], "spread": d[2], "ic": ic})
post = pd.DataFrame(post)
print(f"2025-26 decay window: {len(post)} dates ({post['date'].min()}..{post['date'].max()})", flush=True)

pre.to_csv("var/_analysis/decay_pre2025.csv", index=False)
post.to_csv("var/_analysis/decay_2025_26.csv", index=False)


def summ(name, r, per_year):
    for col in ("exc_top", "exc_bot", "spread"):
        s = r[col].dropna()
        sh = s.mean() / s.std() * np.sqrt(per_year) if s.std() > 0 else 0
        print(f"  {name:9s} {col:8s} mean={s.mean()*100:+7.3f}%  Sharpe={sh:+6.2f}  >0%={ (s>0).mean():4.0%}")
    print(f"  {name:9s} {'ic':8s} mean={r['ic'].dropna().mean():+7.4f}")


print("\n===== C4 DECAY DECOMPOSITION (US) =====")
print("exc_top=traded long-only skill | exc_bot=short-side skill | spread=market-neutral L/S skill")
summ("PRE-2025", pre, 252 / STEP)
summ("2025-26", post, 252 / 21)
print("\nVERDICT GATE: if spread SURVIVES 2025-26 while exc_top decays -> removable factor tilt, "
      "a neutralized basket can restore US (build B1/D1/C2). If spread ALSO collapses -> skill genuinely "
      "lost, stop funding decay-targeted models. (2025-26 model is 6yr-trained across the 2024-25 gap = stale, "
      "the honest live decay scenario.)")
