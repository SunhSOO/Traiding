"""A5 — overnight vs intraday momentum: genuinely NEW information the model has
never seen (it uses only close-to-close; the OPEN field is untouched). The daily
return decomposes close_{t-1}->open_t (overnight) + open_t->close_t (intraday);
literature (Lou-Polk-Skouras) finds the overnight premium PERSISTS while intraday
mean-reverts. Not reconstructible from stored close-to-close returns -> new info.

Builds adj-consistent overnight/intraday cumulative momentum (21/63d) from DB
OHLC, merges onto the 6yr cache, and runs a 3-SEED walk-forward comparing
top-decile EXCESS of {baseline feats} vs {baseline + A5}. Applies every lesson:
top-decile excess (not rank-IC), 3 seeds (not one), winVsBase per fold.

Usage: uv run python var/_analysis/wf_a5_overnight.py US   (or KR)
"""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from sqlalchemy import text
from core.db import session_scope
from scripts.train_lgbm import ALL_FEATURE_COLS

MARKET = sys.argv[1] if len(sys.argv) > 1 else "US"
RET = "ret_fwd_21d"
LGB = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, n_jobs=-1, verbose=-1)
STEP, TRAIN_MIN, TRAIL, EMB, DEC, TOPK = 63, 504, 756, 21, 0.9, 50
SEEDS = [0, 1, 2]
A5 = ["overnight_mom_21", "overnight_mom_63", "intraday_mom_21", "intraday_mom_63", "on_minus_in_21"]

# ── build A5 features from DB OHLC (adj-consistent) ────────────────────────
with session_scope() as s:
    px = pd.DataFrame(s.execute(text(
        "SELECT trade_date, ticker, open, close, adj_close FROM daily_prices WHERE market=:m"),
        {"m": MARKET}).all(), columns=["date", "ticker", "open", "close", "adj_close"])
px["date"] = pd.to_datetime(px["date"])
for c in ("open", "close", "adj_close"):
    px[c] = pd.to_numeric(px[c], errors="coerce")
px = px.sort_values(["ticker", "date"])
adj = (px["adj_close"] / px["close"]).replace([np.inf, -np.inf], np.nan)
px["adj_open"] = px["open"] * adj
gp = px.groupby("ticker", group_keys=False)
prev_adjclose = gp["adj_close"].shift(1)
px["on_ret"] = px["adj_open"] / prev_adjclose - 1.0                 # overnight: prev close -> open
px["in_ret"] = px["adj_close"] / px["adj_open"] - 1.0               # intraday: open -> close
for n in (21, 63):
    px[f"overnight_mom_{n}"] = gp["on_ret"].apply(lambda s: s.rolling(n, min_periods=n // 2).sum())
    px[f"intraday_mom_{n}"] = gp["in_ret"].apply(lambda s: s.rolling(n, min_periods=n // 2).sum())
px["on_minus_in_21"] = px["overnight_mom_21"] - px["intraday_mom_21"]
a5 = px[["date", "ticker"] + A5].copy()
print(f"{MARKET} A5 built: {len(a5)} rows, coverage {a5[A5].notna().all(1).mean():.0%}", flush=True)

# ── merge onto 6yr cache ──────────────────────────────────────────────────
df = pd.read_parquet(f"var/_bt_period_{MARKET}_2018-01-01_2024-01-01.parquet")
df["date"] = pd.to_datetime(df["date"])
base = [c for c in ALL_FEATURE_COLS if c in df.columns]
df = df.merge(a5, on=["date", "ticker"], how="left")
df["mn"] = df[RET] - df.groupby("date")[RET].transform("mean")
allf = base + A5
g = df.groupby("date")
df[allf] = (df[allf] - g[allf].transform("mean")) / (g[allf].transform("std") + 1e-9)
# standalone signal check: does overnight momentum rank-correlate with fwd mn?
for c in A5:
    ic = df.dropna(subset=[c, "mn"]).groupby("date").apply(
        lambda x: spearmanr(x[c], x["mn"], nan_policy="omit").correlation).mean()
    print(f"  standalone daily-IC {c:18s} = {ic:+.4f}", flush=True)

dates = np.sort(df["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
print(f"{MARKET}: {len(df)} rows, {len(reb)} folds, base {len(base)} +A5 {len(A5)}", flush=True)


def exc(p, y):
    sel = p >= np.quantile(p, DEC)
    return (np.nanmean(y[sel]) - np.nanmean(y)) if sel.sum() else np.nan


rows = {(s, v): [] for s in SEEDS for v in ("base", "a5")}
a5_selected = []
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["mn"])
    te = df[df["date"] == t].dropna(subset=[RET])
    if len(tr) < 5000 or len(te) < 30:
        continue
    y = pd.to_numeric(te[RET], errors="coerce").values; sw = np.abs(tr["mn"].values)
    for s in SEEDS:
        for v, feats in (("base", base), ("a5", allf)):
            # top-K selection on this fold's train (production recipe), then fit
            sel = lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[feats].astype(float), tr["mn"], sample_weight=sw)
            top = pd.Series(sel.feature_importances_, index=feats).sort_values(ascending=False).head(TOPK).index.tolist()
            if v == "a5" and s == 0:
                a5_selected.append(sum(c in top for c in A5))
            m = lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[top].astype(float), tr["mn"], sample_weight=sw)
            rows[(s, v)].append(exc(m.predict(te[top].astype(float)), y))

wpy = 252.0 / STEP
print(f"\n{'variant':8s} {'meanExc':>8s} {'ExcSharpe':>10s} {'Exc>0%':>7s} {'winVsBase':>10s}")
for v in ("base", "a5"):
    per = [pd.Series(rows[(s, v)]).dropna() for s in SEEDS]
    mean = np.mean([x.mean() for x in per]); sh = np.mean([x.mean() / x.std() * np.sqrt(wpy) if x.std() > 0 else 0 for x in per])
    pos = np.mean([(x > 0).mean() for x in per])
    if v == "a5":
        wins = np.mean([(np.array(rows[(s, "a5")]) > np.array(rows[(s, "base")])).mean() for s in SEEDS])
        ws = f"{wins:.0%}"
    else:
        ws = "  -"
    print(f"{v:8s} {mean*100:+7.2f}% {sh:+10.2f} {pos:6.0%} {ws:>10s}", flush=True)
print(f"\nA5 features selected into top-50 (seed0, per fold): {a5_selected} "
      f"(mean {np.mean(a5_selected):.1f}/5)")
print("ADOPT if: +A5 mean excess & Sharpe beat base across seeds AND winVsBase>50% AND A5 feats get selected.")
