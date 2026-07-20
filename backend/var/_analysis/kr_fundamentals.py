"""The audit's biggest-untapped axis: DART fundamentals as ORTHOGONAL features on
the confirmed liquid-KR edge. Uses EXISTING financial_facts (KR_LARGE 177/200
covered — no new ingestion). PIT-correct (merge_asof on as_of_ts). Quality/value
block: ROE, ROA, net-margin, gross-profitability(GP/assets, Novy-Marx),
accruals((NI-CFO)/assets, Sloan), leverage, asset-growth. Added to the H42 price
baseline on px_KR_LARGE_PYKRX; adopt only if excess beats price-only both halves.

Usage: uv run python var/_analysis/kr_fundamentals.py
"""
import sys, warnings, json
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from sqlalchemy import text
from core.db import session_scope
from pathlib import Path

UNI = "KR_LARGE_PYKRX"; H, STEP, TRAIN_MIN, TRAIL, DEC = 42, 42, 504, 756, 0.9
SEEDS = [0, 1, 2]
LGB = dict(n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=50,
           subsample=0.7, colsample_bytree=0.7, reg_lambda=5.0, n_jobs=-1, verbose=-1)
CONCEPTS = ["NET_INCOME", "TOTAL_EQUITY", "TOTAL_ASSETS", "REVENUE", "GROSS_PROFIT", "CFO", "TOTAL_LIABILITIES"]

px = pd.read_parquet(Path(f"var/_analysis/px_{UNI}.parquet"))
px["date"] = pd.to_datetime(px["date"]); px = px.sort_values(["ticker", "date"])
codes = list(px["ticker"].unique())

# ── PIT fundamentals from financial_facts (annual) ──────────────────────────
with session_scope() as s:
    rows = s.execute(text(
        "SELECT ticker, concept, value, as_of_ts, period_end FROM financial_facts "
        "WHERE market='KR' AND period_kind='A' AND concept = ANY(:c) AND ticker = ANY(:t)"),
        {"c": CONCEPTS, "t": codes}).all()
ff = pd.DataFrame(rows, columns=["ticker", "concept", "value", "as_of_ts", "period_end"])
ff["value"] = pd.to_numeric(ff["value"], errors="coerce")   # DB Decimal -> float
ff["as_of"] = pd.to_datetime(ff["as_of_ts"]).dt.tz_localize(None)
ff = ff.sort_values(["ticker", "as_of", "period_end"]).drop_duplicates(["ticker", "concept", "as_of"], keep="last")
w = ff.pivot_table(index=["ticker", "as_of"], columns="concept", values="value", aggfunc="last").reset_index()
for c in CONCEPTS:
    if c not in w.columns:
        w[c] = np.nan
w["roe"] = w["NET_INCOME"] / w["TOTAL_EQUITY"].replace(0, np.nan)
w["roa"] = w["NET_INCOME"] / w["TOTAL_ASSETS"].replace(0, np.nan)
w["margin"] = w["NET_INCOME"] / w["REVENUE"].replace(0, np.nan)
w["gross_prof"] = w["GROSS_PROFIT"] / w["TOTAL_ASSETS"].replace(0, np.nan)
w["accruals"] = (w["NET_INCOME"] - w["CFO"]) / w["TOTAL_ASSETS"].replace(0, np.nan)
w["leverage"] = w["TOTAL_LIABILITIES"] / w["TOTAL_EQUITY"].replace(0, np.nan)
w = w.sort_values(["ticker", "as_of"])
w["asset_growth"] = w.groupby("ticker")["TOTAL_ASSETS"].pct_change()
FUND = ["roe", "roa", "margin", "gross_prof", "accruals", "leverage", "asset_growth"]
w = w[["ticker", "as_of"] + FUND].replace([np.inf, -np.inf], np.nan)
print(f"fundamentals: {w['ticker'].nunique()} tickers, {len(w)} annual records", flush=True)

# PIT join: latest fundamental with as_of <= trade date
px = px.sort_values("date")
px = pd.merge_asof(px, w.sort_values("as_of"), left_on="date", right_on="as_of", by="ticker", direction="backward")

# ── price features (H42) ───────────────────────────────────────────────────
px = px.sort_values(["ticker", "date"]); g = px.groupby("ticker", group_keys=False)
def rr(n): return g["close"].apply(lambda s: s.pct_change(n))
for n in (5, 10, 21, 63, 126, 252):
    px[f"ret_{n}"] = rr(n)
px["mom_12_1"] = px["ret_252"] - px["ret_21"]; d1 = g["close"].apply(lambda s: s.pct_change(1)); px["_dret"] = d1.values
gg = px.groupby("ticker", group_keys=False)
for n in (21, 63, 252):
    px[f"vol_{n}"] = gg["_dret"].apply(lambda s: s.rolling(n).std())
px["mom_vadj"] = px["ret_63"] / (px["vol_63"] * np.sqrt(63) + 1e-9)
cc = px["close"]
for wd in (20, 50, 200):
    sma = gg["close"].apply(lambda s: s.rolling(wd).mean()); px[f"px_vs_sma{wd}"] = cc.values / (sma.values + 1e-9) - 1
px["rsi14"] = 50.0  # placeholder to keep dim; replaced below
up = gg["_dret"].apply(lambda s: s.clip(lower=0).rolling(14).mean()); dn = gg["_dret"].apply(lambda s: (-s.clip(upper=0)).rolling(14).mean())
px["rsi14"] = 100 - 100 / (1 + up.values / (dn.values + 1e-9))
px["dollarvol"] = (px["close"] * px["volume"]).astype(float); px["dv_21"] = gg["dollarvol"].apply(lambda s: s.rolling(21).mean())
px["hi_252"] = gg["close"].apply(lambda s: s / (s.rolling(252).max() + 1e-9))
PRICE = ["ret_5", "ret_10", "ret_21", "ret_63", "ret_126", "ret_252", "mom_12_1", "vol_21", "vol_63", "vol_252",
         "mom_vadj", "px_vs_sma20", "px_vs_sma50", "px_vs_sma200", "rsi14", "dv_21", "hi_252"]
px[f"fwd{H}"] = gg["close"].apply(lambda s: s.pct_change(H).shift(-H))
px["mn"] = px[f"fwd{H}"] - px.groupby("date")[f"fwd{H}"].transform("mean")
allf = PRICE + FUND
gd = px.groupby("date"); px[allf] = (px[allf] - gd[allf].transform("mean")) / (gd[allf].transform("std") + 1e-9)
print(f"fundamental coverage in panel: {px[FUND].notna().all(1).mean():.0%}", flush=True)
dates = np.sort(px["date"].unique()); reb = list(range(TRAIN_MIN, len(dates) - H, STEP))

def excf(p, y):
    sel = p >= np.quantile(p, DEC); return (np.nanmean(y[sel]) - np.nanmean(y)) if sel.sum() else np.nan

rows2 = {"price": [], "price+fund": []}; ics = {"price": [], "price+fund": []}
for k in reb:
    t = dates[k]; cut = dates[k - H]; lo = dates[max(0, k - H - TRAIL)]
    tr = px[(px["date"] <= cut) & (px["date"] > lo)].dropna(subset=["mn"]); te = px[px["date"] == t].dropna(subset=[f"fwd{H}"])
    if len(tr) < 3000 or len(te) < 30:
        continue
    y = pd.to_numeric(te[f"fwd{H}"], errors="coerce").values; sw = np.abs(tr["mn"].values)
    for nm, feats in (("price", PRICE), ("price+fund", allf)):
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[feats].astype(float), tr["mn"], sample_weight=sw).predict(te[feats].astype(float)) for s in SEEDS], axis=0)
        rows2[nm].append(excf(p, y)); ics[nm].append(spearmanr(p, te["mn"].values, nan_policy="omit").correlation)

print(f"\n===== {UNI} FUNDAMENTALS on H42 ({len(rows2['price'])} folds, 3-seed) =====")
for nm in ("price", "price+fund"):
    e = np.array(rows2[nm]); h = len(e) // 2
    print(f"  {nm:12s} ic={np.nanmean(ics[nm]):+.4f}  meanExc={e.mean()*100:+.2f}%  H1/H2={np.nanmean(ics[nm][:h]):+.3f}/{np.nanmean(ics[nm][h:]):+.3f}  win={ (e>0).mean():.0%}")
d = np.array(rows2["price+fund"]) - np.array(rows2["price"])
print(f"  delta(fund-price): meanExc {d.mean()*100:+.2f}%  win {(d>0).mean():.0%}   adopt if >0 both halves.")
