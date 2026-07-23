"""RE-AUDIT FOLLOW-UP — the KR incumbent ablation said normalization HURTS KR and
Blitz/rank could help. Before that can overturn the production recipe it must
pass the SAME multi-check rigor I demand of any challenger: split-half
robustness (must win in BOTH halves, not one lucky regime) + cost sensitivity
(30 & 50 bps) + does stacking the improved levers compound or cannibalize.

Continuous 2018-2026 KR production cache, 478 feats, 3-seed. Excess on realized
ret_fwd_21d. A recipe change is ADOPTABLE only if net↑ AND best-2↑ AND wins in
BOTH split-halves AND survives 50bps.

Usage: uv run python var/_analysis/reaudit_kr_recipe.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

MODE = sys.argv[1] if len(sys.argv) > 1 else "light"  # full=step21/no-sub(all 5) · lean=step21/80k(base+no-norm−blitz) · light=step63/40k
FULL = MODE == "full"; LEAN = MODE == "lean"
STEP, TRAIN_MIN, TRAIL, EMB, DEC = (21 if (FULL or LEAN) else 63), 504, 756, 21, 0.9
SEEDS = [0, 1, 2]
SUBSAMPLE = None if FULL else (80000 if LEAN else 40000)   # cap train rows/fold for speed (per-date structure preserved)
LGB = dict(n_estimators=400, num_leaves=31, learning_rate=0.03, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
RET = "ret_fwd_21d"

d0 = pd.read_parquet("var/_bt_period_KR_2018-01-01_2024-01-01.parquet")
d1 = pd.read_parquet("var/_bt_period_KR_2023-06-01_2026-07-01.parquet")
common = [c for c in d0.columns if c in d1.columns]
df = pd.concat([d0[common], d1[common]], ignore_index=True)
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str).str.zfill(6)
df = df.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last").reset_index(drop=True)
feats_all = [c for c in ALL_FEATURE_COLS if c in df.columns]
feats_nb = [c for c in feats_all if "blitz" not in c.lower()]
df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
dates = np.sort(df["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
mid = reb[len(reb) // 2]
print(f"KR recipe re-derivation [{MODE}]: {len(df)} rows, {len(feats_all)} feats, "
      f"step={STEP} subsample={SUBSAMPLE}, {len(reb)} folds (split @ fold {len(reb)//2})", flush=True)


def walk(label="mn", norm=True, feats=None):
    feats = feats or feats_all
    lab = {"mn": "mn", "rank": "rank_fwd_21d"}[label]
    rows = []  # (foldidx, excess, turnover_prev, bench)
    prev = None
    for i, k in enumerate(reb):
        t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=[lab, RET]).copy()
        te = df[df["date"] == t].dropna(subset=[RET]).copy()
        if len(tr) < 5000 or len(te) < 30:
            continue
        if SUBSAMPLE and len(tr) > SUBSAMPLE:
            tr = tr.sample(SUBSAMPLE, random_state=k).sort_values("date")
        if norm:
            gtr = tr.groupby("date"); tr[feats] = (tr[feats] - gtr[feats].transform("mean")) / (gtr[feats].transform("std") + 1e-9)
            sub = te[feats]; te[feats] = (sub - sub.mean()) / (sub.std() + 1e-9)
        sw = np.abs(tr[lab].values)
        y = pd.to_numeric(te[RET], errors="coerce").values; tk = te["ticker"].values
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[feats].astype(float), tr[lab], sample_weight=sw).predict(te[feats].astype(float)) for s in SEEDS], axis=0)
        s_ = p >= np.quantile(p, DEC)
        exc = float(np.nanmean(y[s_]) - np.nanmean(y))
        cur = set(tk[s_]); turn = 1 - len(cur & prev) / len(cur | prev) if prev else np.nan; prev = cur
        rows.append((k, exc, turn, float(np.nanmean(y))))
    R = np.array(rows)
    def stats(sub):
        e = sub[:, 1]; turn = np.nanmean(sub[:, 2]); bench = sub[:, 3]
        n30 = e.mean() - (turn if turn == turn else 0) * 2 * 30 / 1e4
        n50 = e.mean() - (turn if turn == turn else 0) * 2 * 50 / 1e4
        b2 = np.mean(np.sort(e)[:-2]); bear = np.nanmean(e[bench <= np.quantile(bench, 1/3)])
        return n30, n50, b2, bear
    h1 = stats(R[R[:, 0] < mid]); h2 = stats(R[R[:, 0] >= mid]); full = stats(R)
    return dict(net30=full[0], net50=full[1], b2=full[2], bear=full[3], h1net=h1[0], h2net=h2[0])


VARS = [
    ("base mn+norm+blitz", dict()),
    ("no-norm", dict(norm=False)),
    ("no-norm +rank", dict(norm=False, label="rank")),
    ("no-norm −blitz", dict(norm=False, feats=feats_nb)),
    ("no-norm +rank −blitz", dict(norm=False, label="rank", feats=feats_nb)),
]
if LEAN:
    VARS = [VARS[0], VARS[3]]   # ship-gate pair only: base vs no-norm −blitz
print(f"\n===== KR RECIPE RE-DERIVATION (3-seed) — adoptable iff net↑ & best-2↑ & BOTH halves↑ & survives 50bps =====")
print(f"{'recipe':22s} {'net@30':>7s} {'net@50':>7s} {'best-2':>8s} {'bear':>7s} {'H1net':>7s} {'H2net':>7s} {'verdict':>9s}")
base = None
for name, kw in VARS:
    r = walk(**kw)
    if base is None:
        base = r
    win = (r["net30"] > base["net30"] and r["b2"] > base["b2"] and r["h1net"] > base["h1net"] and r["h2net"] > base["h2net"] and r["net50"] > base["net50"])
    v = "BASE" if name.startswith("base") else ("ADOPT" if win else "no")
    print(f"{name:22s} {r['net30']*100:+6.2f}% {r['net50']*100:+6.2f}% {r['b2']*100:+7.2f}% {r['bear']*100:+6.2f}% "
          f"{r['h1net']*100:+6.2f}% {r['h2net']*100:+6.2f}% {v:>9s}", flush=True)
print("  H1=2018~2022, H2=2022~2026. ADOPT requires winning base on net@30, net@50, best-2, AND both halves.")
