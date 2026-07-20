"""C4v2 — the HONEST US decay test, now that the 2024-25 gap is filled.

C4 applied a STALE 6yr model (trained <=2023-12) forward to 2025-26, so it
couldn't separate 'model is stale' from 'skill is gone'. C4v2 concatenates the
2018-2024 + 2023-2026 caches into continuous 2018-2026 coverage and runs a
ROLLING-RETRAIN walk-forward (retrain each fold on trailing data, embargo 21d)
straight THROUGH the 2024-2026 decay window — the real live scenario (the prod
model retrains weekly, it is never 2yr stale).

Per fold: exc_top / exc_bottom / spread (market-neutral L/S) / IC, decomposed
pre-2024 vs 2024-2026. If a CONTINUOUSLY-RETRAINED model's 2024-26 top-decile
excess & spread hold up -> the 'decay' was a STALENESS artifact (weekly retrain,
already ON, fixes it) -> US is fine. If they collapse even with fresh retrain ->
a genuine regime break. Mirage-proof (realized returns). Per-quarter breakout
guards against the tail-concentration that fooled C4's aggregate.

Usage: uv run python var/_analysis/decay_v2_us.py
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

RET = "ret_fwd_21d"
LGB = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
STEP, TRAIN_MIN, TRAIL, EMB, DEC = 63, 504, 756, 21, 0.1

old = pd.read_parquet("var/_bt_period_US_2018-01-01_2024-01-01.parquet")
new = pd.read_parquet("var/_bt_period_US_2023-06-01_2026-07-01.parquet")
feats = [c for c in ALL_FEATURE_COLS if c in old.columns and c in new.columns]
keep = ["date", "ticker", RET] + feats
old["_src"], new["_src"] = 0, 1
df = pd.concat([old[keep + ["_src"]], new[keep + ["_src"]]], ignore_index=True)
df["date"] = pd.to_datetime(df["date"])
# overlap: keep the NEWER cache's rows (forward labels computed with more data)
df = df.sort_values(["date", "ticker", "_src"]).drop_duplicates(["date", "ticker"], keep="last")
df = df.sort_values("date").reset_index(drop=True)
df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
g = df.groupby("date")
df[feats] = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
print(f"US continuous: {len(df)} rows, {len(feats)} feats, {df['date'].min().date()}..{df['date'].max().date()}", flush=True)

dates = np.sort(df["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))


def decomp(p, y):
    hi = np.quantile(p, 1 - DEC); lo = np.quantile(p, DEC); um = y.mean()
    return y[p >= hi].mean() - um, um - y[p <= lo].mean(), (y[p >= hi].mean() - y[p <= lo].mean())


rows = []
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["mn"])
    te = df[df["date"] == t].dropna(subset=[RET])
    if len(tr) < 5000 or len(te) < 30:
        continue
    m = lgb.LGBMRegressor(**LGB).fit(tr[feats].astype(float), tr["mn"].astype(float),
                                     sample_weight=np.abs(tr["mn"].values))
    p = m.predict(te[feats].astype(float)); y = pd.to_numeric(te[RET], errors="coerce").values
    et, eb, sp = decomp(p, y)
    ic = spearmanr(p, te["mn"].values, nan_policy="omit").correlation
    rows.append({"date": pd.Timestamp(t).date(), "exc_top": et, "exc_bot": eb, "spread": sp, "ic": ic})
    print(f"  {rows[-1]['date']} top={et:+.4f} spread={sp:+.4f} ic={ic:+.3f}", flush=True)

r = pd.DataFrame(rows); r["date"] = pd.to_datetime(r["date"])
r.to_csv("var/_analysis/decay_v2_us.csv", index=False)
wpy = 252.0 / STEP


def summ(name, s):
    for col in ("exc_top", "exc_bot", "spread"):
        x = s[col].dropna()
        sh = x.mean() / x.std() * np.sqrt(wpy) if x.std() > 0 else 0
        print(f"  {name:10s} {col:8s} mean={x.mean()*100:+7.3f}%  Sharpe={sh:+6.2f}  >0%={(x>0).mean():4.0%}  n={len(x)}")
    print(f"  {name:10s} ic       mean={s['ic'].dropna().mean():+7.4f}")


pre = r[r["date"] < "2024-01-01"]; dec = r[r["date"] >= "2024-01-01"]
print(f"\n===== C4v2 CONTINUOUS-RETRAIN DECAY DECOMPOSITION (US) =====")
summ("PRE-2024", pre)
summ("2024-26", dec)
print("\n  per-quarter 2024-26 (tail-concentration guard):")
dq = dec.copy(); dq["q"] = dq["date"].dt.to_period("Q")
print(dq.groupby("q")[["exc_top", "spread", "ic"]].mean().round(4).to_string())
print("\nVERDICT: if 2024-26 top-decile excess & spread HOLD with rolling retrain -> decay was STALENESS "
      "(weekly retrain fixes it, US fine). If they collapse even fresh -> genuine regime break. Check the "
      "per-quarter row: reject a healthy aggregate that is concentrated in 1-2 quarters.")
