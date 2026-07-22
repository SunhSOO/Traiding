"""RE-AUDIT CATEGORY A/B — the incumbent production recipe, held to the SAME bar
we killed challengers with (3-seed + best-2-drop + conc5 + bear), which it was
never subjected to (those gates postdate its adoption). Continuous 2018-2026 KR
production cache (478 features, KOSPI200+KOSDAQ150 = liquid → yfinance reliable).

Ablation: each adopted lever removed one at a time; if removing it does NOT hurt
(or helps) under the full battery, that lever was never actually earning its
place. Excess measured on realized ret_fwd_21d (label-agnostic) so label
variants compare fairly. net-of-cost @30bps/side.

Variants:
  base       = mn label · per-date z · |label| weight · all-478         (recipe)
  label_raw  = ret_fwd_21d label instead of mn
  label_rank = rank_fwd_21d label
  no_norm    = skip per-date z-score
  no_weight  = uniform sample weight
  no_blitz   = drop Blitz residual-momentum features
  top50      = top-50 importance selection (vs all-478)

Usage: uv run python var/_analysis/wf_incumbent_kr.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

STEP, TRAIN_MIN, TRAIL, EMB, DEC, BPS = 21, 504, 756, 21, 0.9, 30
SEEDS = [0, 1, 2]
LGB = dict(n_estimators=400, num_leaves=31, learning_rate=0.03, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
RET = "ret_fwd_21d"

MKT = sys.argv[1] if len(sys.argv) > 1 else "KR"
d0 = pd.read_parquet(f"var/_bt_period_{MKT}_2018-01-01_2024-01-01.parquet")
d1 = pd.read_parquet(f"var/_bt_period_{MKT}_2023-06-01_2026-07-01.parquet")
common = [c for c in d0.columns if c in d1.columns]
df = pd.concat([d0[common], d1[common]], ignore_index=True)
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str).str.zfill(6)
df = df.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last").reset_index(drop=True)
feats_all = [c for c in ALL_FEATURE_COLS if c in df.columns]
blitz = [c for c in feats_all if "blitz" in c.lower()]
feats_noblitz = [c for c in feats_all if c not in blitz]
df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
print(f"KR incumbent: {len(df)} rows, {len(feats_all)} feats ({len(blitz)} blitz: {blitz}), "
      f"{df['date'].min().date()}..{df['date'].max().date()}", flush=True)

dates = np.sort(df["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))


def walk(label="mn", norm=True, weight=True, feats=None, topk=None):
    feats = feats or feats_all
    lab = {"mn": "mn", "raw": RET, "rank": "rank_fwd_21d"}[label]
    e, turns, bench, ics = [], [], [], []; prev = None
    for k in reb:
        t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=[lab, RET]).copy()
        te = df[df["date"] == t].dropna(subset=[RET]).copy()
        if len(tr) < 5000 or len(te) < 30:
            continue
        if norm:
            gtr = tr.groupby("date"); tr[feats] = (tr[feats] - gtr[feats].transform("mean")) / (gtr[feats].transform("std") + 1e-9)
            te = te.copy(); sub = te[feats]; te[feats] = (sub - sub.mean()) / (sub.std() + 1e-9)
        sw = np.abs(tr[lab].values) if weight else None
        y = pd.to_numeric(te[RET], errors="coerce").values; tk = te["ticker"].values
        use = feats
        if topk:
            sel0 = lgb.LGBMRegressor(**LGB, random_state=0).fit(tr[feats].astype(float), tr[lab], sample_weight=sw)
            use = pd.Series(sel0.feature_importances_, index=feats).sort_values(ascending=False).head(topk).index.tolist()
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[use].astype(float), tr[lab], sample_weight=sw).predict(te[use].astype(float)) for s in SEEDS], axis=0)
        s_ = p >= np.quantile(p, DEC)
        e.append(float(np.nanmean(y[s_]) - np.nanmean(y)))
        cur = set(tk[s_]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else np.nan); prev = cur
        bench.append(float(np.nanmean(y))); ics.append(spearmanr(p, te["mn"].values, nan_policy="omit").correlation)
    e = np.array(e); bench = np.array(bench); turn = np.nanmean(turns); ppy = 252.0 / STEP
    net = e.mean() - (turn if turn == turn else 0) * 2 * BPS / 1e4
    b2 = np.mean(np.sort(e)[:-2]); pos = e[e > 0].sum(); conc5 = np.sort(e)[::-1][:5].clip(min=0).sum() / pos if pos > 0 else np.nan
    bear = np.nanmean(e[bench <= np.quantile(bench, 1/3)])
    return dict(ic=np.nanmean(ics), gross=e.mean(), net=net, net_ann=net * ppy, b2=b2, conc5=conc5, bear=bear, pos=(e > 0).mean(), n=len(e))


VARS = [
    ("base (recipe)", dict()),
    ("−mn → raw", dict(label="raw")),
    ("−mn → rank", dict(label="rank")),
    ("−normalize", dict(norm=False)),
    ("−|label| weight", dict(weight=False)),
    ("−Blitz feats", dict(feats=feats_noblitz)),
    ("+top50 select", dict(topk=50)),
]
print(f"\n===== KR INCUMBENT ABLATION ({len(reb)} rebalances, 3-seed, {BPS}bps) =====")
print(f"{'variant':18s} {'ic':>8s} {'grossExc':>9s} {'net':>7s} {'net_ANN':>8s} {'best-2':>8s} {'conc5':>6s} {'bear':>7s} {'pos':>4s}")
base = None
for name, kw in VARS:
    r = walk(**kw)
    if name.startswith("base"):
        base = r
    dnet = "" if base is None or name.startswith("base") else f"  ({(r['net']-base['net'])*100:+.2f} vs base)"
    print(f"{name:18s} {r['ic']:+8.4f} {r['gross']*100:+8.2f}% {r['net']*100:+6.2f}% {r['net_ann']*100:+7.1f}% "
          f"{r['b2']*100:+7.2f}% {r['conc5']:5.0%} {r['bear']*100:+6.2f}% {r['pos']:3.0%}{dnet}", flush=True)
print("  read: a lever is JUSTIFIED if removing it (−lever) HURTS net/best-2/bear vs base. If '−lever' ≈/> base, the lever was never earning its place.")
