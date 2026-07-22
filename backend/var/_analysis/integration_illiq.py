"""TEST 3 — does the production recipe UNDER-EXPLOIT the illiquidity premium?
ortho-lite: amihud-raw tilt net +2.53% (turn 20%) BEATS the 478-model's +1.01% in the
production universe. Hypothesis: the mn-label + per-date normalization wash out the
illiquidity premium (double-normalizing already-z'd amihud, and mn removing a systematic
premium), so the model's other features don't recover it. Test on a focused ~24-feature
set (production-faithful recipe: mn label, per-date z, |label| weight):
  FULL          all ~24 feats
  NO-AMIHUD     drop the 4 amihud feats     -> if ~= FULL, amihud adds nothing INSIDE the model
  AMIHUD-ONLY   only the 4 amihud feats     -> if strong, the premium is real but the model buries it
  + amihud feature importance rank in FULL
  + no-norm FULL/AMIHUD-ONLY  (does dropping the harmful KR normalization un-bury it?)
Light: ~27 cols loaded, step63, 2-seed, 40k subsample. Safe beside the batch.
Usage: uv run python var/_analysis/integration_illiq.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr

AMIHUD = ["amihud_illiquidity", "amihud_illiq_21d", "amihud_illiq_63d", "amihud_illiq_z_60d"]
PRICE = ["ret_5d", "ret_21d", "ret_63d", "ret_126d", "ret_252d",  # NOTE: vol_adj_ret_21d REMOVED (IC 0.94 w/ fwd = leaked/forward col, not in production ALL_FEATURE_COLS)
         "vol_21d", "vol_63d", "vol_252d", "atr_pct", "realised_vol_21d",
         "px_vs_sma20", "px_vs_sma50", "px_vs_sma200", "rsi14", "macd_hist", "bb_pctb",
         "dollar_volume_z63", "volume_z63", "ret_21d_xrank"]
FEATS = PRICE + AMIHUD
COLS = ["date", "ticker", "ret_fwd_21d"] + FEATS
STEP, TRAIN_MIN, TRAIL, EMB, DEC, BPS, SUB = 63, 504, 756, 21, 0.9, 30, 40000
SEEDS = [0, 1]
LGB = dict(n_estimators=400, num_leaves=31, learning_rate=0.03, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)

d0 = pd.read_parquet("var/_bt_period_KR_2018-01-01_2024-01-01.parquet", columns=COLS)
d1 = pd.read_parquet("var/_bt_period_KR_2023-06-01_2026-07-01.parquet", columns=COLS)
df = pd.concat([d0, d1], ignore_index=True)
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
df = df.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last").reset_index(drop=True)
df["mn"] = df["ret_fwd_21d"] - df.groupby("date")["ret_fwd_21d"].transform("mean")
dates = np.sort(df["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
print(f"integration: {len(df)} rows, {len(FEATS)} feats ({len(AMIHUD)} amihud)", flush=True)


def walk(feats, norm=True, want_imp=False):
    e, turns, bench = [], [], []; prev = None; imps = []
    for k in reb:
        t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["mn", "ret_fwd_21d"]).copy()
        te = df[df["date"] == t].dropna(subset=["ret_fwd_21d"]).copy()
        if len(tr) < 4000 or len(te) < 25:
            continue
        if len(tr) > SUB:
            tr = tr.sample(SUB, random_state=k)
        if norm:
            g = tr.groupby("date"); tr[feats] = (tr[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
            sub = te[feats]; te[feats] = (sub - sub.mean()) / (sub.std() + 1e-9)
        sw = np.abs(tr["mn"].values); y = te["ret_fwd_21d"].values; tk = te["ticker"].values
        ps = []
        for s in SEEDS:
            m = lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[feats].astype(float), tr["mn"], sample_weight=sw)
            ps.append(m.predict(te[feats].astype(float)))
            if want_imp:
                imps.append(pd.Series(m.feature_importances_, index=feats))
        p = np.mean(ps, axis=0); sel = p >= np.quantile(p, DEC)
        e.append(float(np.nanmean(y[sel]) - np.nanmean(y)))
        cur = set(tk[sel]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else np.nan); prev = cur
        bench.append(float(np.nanmean(y)))
    e = np.array(e); bench = np.array(bench); turn = np.nanmean(turns); mid = len(e) // 2
    net = e.mean() - (turn if turn == turn else 0) * 2 * BPS / 1e4
    b2 = np.mean(np.sort(e)[:-2]); bear = np.nanmean(e[bench <= np.quantile(bench, 1/3)])
    imp = None
    if want_imp:
        im = pd.concat(imps, axis=1).mean(axis=1).sort_values(ascending=False)
        rk = {f: list(im.index).index(f) + 1 for f in AMIHUD}
        imp = f"amihud importance ranks (of {len(feats)}): " + ", ".join(f"{f.split('_')[-1]}=#{rk[f]}" for f in AMIHUD)
    return dict(net=net, gross=e.mean(), turn=turn, b2=b2, bear=bear, h1=np.mean(e[:mid]), h2=np.mean(e[mid:]), imp=imp)


print(f"\n===== ILLIQ INTEGRATION (production universe, step{STEP}, {BPS}bps) =====")
print(f"{'variant':22s} {'net':>7s} {'gross':>7s} {'turn':>5s} {'best-2':>7s} {'bear':>7s} {'H1':>7s} {'H2':>7s}")
rows = [("FULL (norm)", FEATS, True, True), ("NO-AMIHUD (norm)", PRICE, True, False),
        ("AMIHUD-ONLY (norm)", AMIHUD, True, False), ("FULL (no-norm)", FEATS, False, False),
        ("AMIHUD-ONLY (no-norm)", AMIHUD, False, False)]
imp_line = None
for name, feats, norm, wi in rows:
    r = walk(feats, norm=norm, want_imp=wi)
    if r["imp"]:
        imp_line = r["imp"]
    print(f"{name:22s} {r['net']*100:+6.2f}% {r['gross']*100:+6.2f}% {r['turn']:4.0%} {r['b2']*100:+6.2f}% "
          f"{r['bear']*100:+6.2f}% {r['h1']*100:+6.2f}% {r['h2']*100:+6.2f}%", flush=True)
print(f"\n  {imp_line}")
print("  ref: amihud-raw tilt (no model) net +2.53%. read: if FULL≈NO-AMIHUD and AMIHUD-ONLY strong → model buries the illiquidity premium.")
