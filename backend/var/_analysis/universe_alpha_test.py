"""Test whether cross-sectional SELECTION alpha lives in LESS-efficient (smaller
cap) universes. Same lean price-feature model + same rigorous bar (walk-forward
top-decile EXCESS, 3-seed) applied across a cap spectrum. Hypothesis: excess/IC
rises as we go down the cap ladder (large -> mid -> small) where inefficiency is
larger. This is a UNIVERSE test, not a signal tweak — the one lever that could
raise the alpha ceiling the mega-cap universe caps.

Prices via yfinance (auto-adjusted), cached per universe. Lean features are
price/volume-only (computable for any ticker), cross-sectionally z-scored.

Usage: uv run python var/_analysis/universe_alpha_test.py US_MID
"""
import sys, json, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
from pathlib import Path

UNI = sys.argv[1]
CAP = 350          # cap universe size to bound the yfinance fetch
STEP, TRAIN_MIN, TRAIL, EMB, DEC = 63, 504, 756, 21, 0.9
SEEDS = [0, 1, 2]
LGB = dict(n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=50,
           subsample=0.7, colsample_bytree=0.7, reg_lambda=5.0, n_jobs=-1, verbose=-1)

tickers = json.load(open("var/_analysis/universes.json"))[UNI]
if len(tickers) > CAP:
    tickers = tickers[:CAP]      # keep the cap ordering (already marcap/index-ranked)
raw_path = Path(f"var/_analysis/px_{UNI}.parquet")

# ── prices (cached) ────────────────────────────────────────────────────────
if raw_path.exists():
    px = pd.read_parquet(raw_path)
    print(f"[{UNI}] loaded cached prices {px.shape}", flush=True)
else:
    import yfinance as yf
    print(f"[{UNI}] downloading {len(tickers)} tickers via yfinance...", flush=True)
    data = yf.download(tickers, start="2018-01-01", end="2026-07-01", auto_adjust=True,
                       threads=True, progress=False)
    # data columns are a MultiIndex (field, ticker). Reshape each field to long.
    def long(field):
        d = data[field]
        if isinstance(d, pd.Series):
            d = d.to_frame()
        m = d.stack().rename(field.lower())
        m.index = m.index.set_names(["date", "ticker"])
        return m.reset_index()
    px = long("Close")
    for f in ("High", "Low", "Volume"):
        px = px.merge(long(f), on=["date", "ticker"], how="left")
    px["date"] = pd.to_datetime(px["date"])
    # keep tickers with enough history
    cnt = px.groupby("ticker")["close"].transform("size")
    px = px[cnt >= 600].reset_index(drop=True)
    px.to_parquet(raw_path)
    print(f"[{UNI}] fetched {px['ticker'].nunique()} usable tickers, {len(px)} rows", flush=True)

# ── optional liquidity floor: keep tickers above a $-volume percentile ─────
LIQ = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
if LIQ > 0:
    dv = (px["close"] * px["volume"]).groupby(px["ticker"]).median()
    keep = dv[dv >= dv.quantile(LIQ)].index
    px = px[px["ticker"].isin(keep)]
    print(f"[{UNI}] liquidity floor {LIQ:.0%}: kept {len(keep)} tickers", flush=True)

# ── lean cross-sectional price features ────────────────────────────────────
px = px.sort_values(["ticker", "date"])
g = px.groupby("ticker", group_keys=False)
c = px["close"]
def ret(n): return g["close"].apply(lambda s: s.pct_change(n))
px["ret_5"] = ret(5); px["ret_10"] = ret(10); px["ret_21"] = ret(21)
px["ret_63"] = ret(63); px["ret_126"] = ret(126); px["ret_252"] = ret(252)
px["mom_12_1"] = px["ret_252"] - px["ret_21"]
dret = g["close"].apply(lambda s: s.pct_change(1))
px["_dret"] = dret.values
gg = px.groupby("ticker", group_keys=False)
px["vol_21"] = gg["_dret"].apply(lambda s: s.rolling(21).std())
px["vol_63"] = gg["_dret"].apply(lambda s: s.rolling(63).std())
px["vol_252"] = gg["_dret"].apply(lambda s: s.rolling(252).std())
px["mom_vadj"] = px["ret_63"] / (px["vol_63"] * np.sqrt(63) + 1e-9)
for w in (20, 50, 200):
    sma = gg["close"].apply(lambda s: s.rolling(w).mean())
    px[f"px_vs_sma{w}"] = c.values / (sma.values + 1e-9) - 1.0
px["sma50_200"] = gg["close"].apply(lambda s: s.rolling(50).mean() / (s.rolling(200).mean() + 1e-9) - 1.0)
# RSI14
delta = px["_dret"].values
up = gg["_dret"].apply(lambda s: s.clip(lower=0).rolling(14).mean())
dn = gg["_dret"].apply(lambda s: (-s.clip(upper=0)).rolling(14).mean())
px["rsi14"] = 100 - 100 / (1 + up.values / (dn.values + 1e-9))
# liquidity
px["dollarvol"] = (px["close"] * px["volume"]).astype(float)
px["dv_21"] = gg["dollarvol"].apply(lambda s: s.rolling(21).mean())
px["amihud"] = gg.apply(lambda d: (d["_dret"].abs() / (d["dollarvol"] + 1e3)).rolling(21).mean()).values
px["hl_range"] = gg.apply(lambda d: ((d["high"] - d["low"]) / d["close"]).rolling(21).mean()).values
px["hi_252_prox"] = gg["close"].apply(lambda s: s / (s.rolling(252).max() + 1e-9))
# label: 21d fwd return, market-neutral per date
px["fwd21"] = gg["close"].apply(lambda s: s.pct_change(21).shift(-21))

FEATS = ["ret_5", "ret_10", "ret_21", "ret_63", "ret_126", "ret_252", "mom_12_1",
         "vol_21", "vol_63", "vol_252", "mom_vadj", "px_vs_sma20", "px_vs_sma50",
         "px_vs_sma200", "sma50_200", "rsi14", "dv_21", "amihud", "hl_range", "hi_252_prox"]
px["date"] = pd.to_datetime(px["date"])
px["mn"] = px["fwd21"] - px.groupby("date")["fwd21"].transform("mean")
gd = px.groupby("date")
px[FEATS] = (px[FEATS] - gd[FEATS].transform("mean")) / (gd[FEATS].transform("std") + 1e-9)

dates = np.sort(px["date"].unique())
reb = list(range(TRAIN_MIN, len(dates) - EMB, STEP))
avg_names = px.dropna(subset=["mn"]).groupby("date").size().median()
print(f"[{UNI}] {px['ticker'].nunique()} tickers, median {avg_names:.0f} names/date, {len(reb)} folds", flush=True)


def exc(p, y):
    sel = p >= np.quantile(p, DEC)
    return (np.nanmean(y[sel]) - np.nanmean(y)) if sel.sum() else np.nan


COST = 0.0020 if UNI.endswith("LARGE") else (0.0050 if UNI.endswith("SMALL") else 0.0035)  # small-caps cost more
rows = {s: [] for s in SEEDS}
ics, turns, prevset = [], [], None
for k in reb:
    t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
    tr = px[(px["date"] <= cut) & (px["date"] > lo)].dropna(subset=["mn"])
    te = px[px["date"] == t].dropna(subset=["fwd21"])
    if len(tr) < 3000 or len(te) < 30:
        continue
    y = pd.to_numeric(te["fwd21"], errors="coerce").values; sw = np.abs(tr["mn"].values)
    tk = te["ticker"].values
    for s in SEEDS:
        m = lgb.LGBMRegressor(**LGB, random_state=s).fit(tr[FEATS].astype(float), tr["mn"], sample_weight=sw)
        p = m.predict(te[FEATS].astype(float))
        rows[s].append(exc(p, y))
        if s == 0:
            ics.append(spearmanr(p, te["mn"].values, nan_policy="omit").correlation)
            cur = set(tk[p >= np.quantile(p, DEC)])
            turns.append(1.0 - len(cur & prevset) / len(cur | prevset) if prevset else np.nan)
            prevset = cur

wpy = 252.0 / STEP
per = [pd.Series(rows[s]).dropna() for s in SEEDS]
mean = np.mean([x.mean() for x in per]); sh = np.mean([x.mean() / x.std() * np.sqrt(wpy) if x.std() > 0 else 0 for x in per])
pos = np.mean([(x > 0).mean() for x in per])
ic = np.nanmean(ics)
icser = pd.Series(ics); h = len(icser) // 2
ic_h1, ic_h2 = icser.iloc[:h].mean(), icser.iloc[h:].mean()          # sub-period stability
turn = np.nanmean(turns)
exc_s0 = pd.Series(rows[0]).dropna()
net = exc_s0.mean() - turn * 2 * COST                                # net-of-cost (seed0)
print(f"\n===== {UNI}: median {avg_names:.0f} names/date, {len(per[0])} folds =====")
print(f"  meanExcess(3-seed) = {mean*100:+.3f}%   ExcSharpe = {sh:+.2f}   Exc>0% = {pos:.0%}   meanIC = {ic:+.4f}")
print(f"  IC stability: H1 {ic_h1:+.4f} / H2 {ic_h2:+.4f}   turnover {turn:.2f}")
gx = exc_s0.mean()
sweep = "  net/period @roundtrip: " + "  ".join(
    f"{rt}bp={100*(gx - turn*rt/1e4):+.2f}%" for rt in (30, 60, 100, 150, 250))
print(sweep + f"   (gross {gx*100:+.2f}%, turn {turn:.2f})")
print(f"  NOTE survivorship: current-membership tickers only (past failures excluded) -> upside biased "
      f"(worse for micro-caps w/ higher delisting).")
