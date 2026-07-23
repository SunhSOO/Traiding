"""SPECIALIZED SOLUTION per tradeable universe — signal specialization + full gauntlet
+ cost + capacity (user 2026-07-23: build a validated specialized solution for each
universe, don't skip, consider all factors incl. data gates). Layer 1 = price-only
signal library (works for all 9 universes uniformly). Fundamentals layer added later
for KR_LARGE/MID.

Per universe, per signal (single-name sort) AND lgbm-combine (trailing WF, 3-seed):
  top-decile excess GROSS + NET(universe cost) + rank-IC + best-2(drop 2 best folds)
  + conc5(top-5-fold share of +excess; >70%=artifact) + bear(bottom-tercile mkt folds)
  + H1/H2 split-half + turnover + capacity(median $ADV of selected decile).

Signals (15, oriented IC>0 = predicts higher fwd 21d):
  STR REV_1M MOM_12_1 MOM_6_1 HI52 ILLIQ21 ILLIQ63 ZERORET LOTTO MAX5 LOWVOL IVOL SIZE ISKEW DOWNVOL
Usage: uv run python var/_analysis/universe_specialize.py [UNI ...]
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr

UNIS = [  # name, cache, source, cost_bps/side, currency-scale-note
    ("US_LARGE", "px_US_LARGE", "yf", 15), ("US_MID", "px_US_MID", "yf", 20),
    ("US_SMALL", "px_US_SMALL", "yf", 25), ("US_BROAD", "px_US_BROAD", "yf", 25),
    ("KR_LARGE", "px_KR_LARGE_PYKRX", "pykrx", 30), ("KR_MID", "px_KR_MID_PYKRX", "pykrx", 40),
    ("KR_SMALL", "px_KR_SMALL", "yf!", 60), ("KR_MICRO", "px_KR_MICRO_PYKRX", "pykrx", 120),
    ("TW_SMALL", "px_TW_SMALL", "yf!", 60),
    # 2nd-source variants (yfinance) for data-artifact cross-check vs pykrx:
    ("KR_LARGE_YF", "px_KR_LARGE", "yf-2nd", 30), ("KR_MID_YF", "px_KR_MID", "yf-2nd", 40),
    ("KR_MICRO_YF", "px_KR_MICRO", "yf-2nd", 120),
]
SIGS = ["STR", "REV_1M", "MOM_12_1", "MOM_6_1", "HI52", "ILLIQ21", "ILLIQ63", "ILLIQ126", "ZERORET",
        "LOTTO", "LOWVOL", "IVOL", "SIZE", "ISKEW", "DOWNVOL"]   # MAX5 dropped (redundant w/ LOTTO, slow)
STEP, DEC, TRAIL, TRAIN_MIN, EMB = 21, 0.9, 756, 504, 21
SEEDS = [0, 1, 2]
LGB = dict(n_estimators=300, num_leaves=31, learning_rate=0.03, min_child_samples=80,
           subsample=0.7, colsample_bytree=0.7, reg_lambda=5.0, n_jobs=-1, verbose=-1)


def build(df):
    df = df.sort_values(["date", "ticker"]).copy()
    df["ret1"] = df.groupby("ticker")["close"].pct_change()
    df["mkt"] = df.groupby("date")["ret1"].transform("mean")
    df["dv"] = (df["close"] * df["volume"]).astype(float)
    df = df.sort_values(["ticker", "date"]); g = df.groupby("ticker", group_keys=False)
    r = lambda n: g["close"].transform(lambda s: s.pct_change(n))
    df["STR"] = -r(5); df["REV_1M"] = -r(21)
    df["MOM_12_1"] = g["close"].shift(21) / g["close"].shift(252) - 1
    df["MOM_6_1"] = g["close"].shift(21) / g["close"].shift(126) - 1
    df["HI52"] = df["close"] / g["close"].transform(lambda s: s.rolling(252, min_periods=120).max())
    df["_ai"] = df["ret1"].abs() / df["dv"].replace(0, np.nan)   # transform (index-safe), NOT apply+reset_index
    df["ILLIQ21"] = g["_ai"].transform(lambda s: s.rolling(21, min_periods=10).mean())
    df["ILLIQ63"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
    df["ILLIQ126"] = g["_ai"].transform(lambda s: s.rolling(126, min_periods=63).mean())
    df["ZERORET"] = g["ret1"].transform(lambda s: (s.abs() < 1e-9).rolling(21, min_periods=10).mean())
    df["LOTTO"] = -g["ret1"].transform(lambda s: s.rolling(21, min_periods=10).max())
    df["LOWVOL"] = -g["ret1"].transform(lambda s: s.rolling(60, min_periods=30).std())
    df["resid"] = df["ret1"] - df["mkt"]
    df["IVOL"] = -g["resid"].transform(lambda s: s.rolling(60, min_periods=30).std())
    df["SIZE"] = -np.log(g["dv"].transform(lambda s: s.rolling(63, min_periods=20).median()).clip(lower=1))
    df["ISKEW"] = -g["resid"].transform(lambda s: s.rolling(60, min_periods=30).skew())
    df["_neg2"] = df["ret1"].clip(upper=0) ** 2   # vectorized downside deviation
    df["DOWNVOL"] = -np.sqrt(g["_neg2"].transform(lambda s: s.rolling(60, min_periods=30).mean()))
    df["fwd"] = g["close"].shift(-21) / df["close"] - 1
    df["mn"] = df["fwd"] - df.groupby("date")["fwd"].transform("mean")
    df["advM"] = g["dv"].transform(lambda s: s.rolling(21, min_periods=10).median())
    return df


def metrics(rows, cost):
    R = pd.DataFrame(rows, columns=["k", "exc", "turn", "bench", "cap", "ic"])
    e = R["exc"].values; turn = R["turn"].mean(skipna=True) if R["turn"].notna().any() else np.nan
    net = e.mean() - (turn if turn == turn else 0) * 2 * cost / 1e4
    b2 = np.mean(np.sort(e)[:-2]) if len(e) > 2 else e.mean()
    pos = e[e > 0].sum(); conc5 = np.sort(e)[::-1][:5].clip(min=0).sum() / pos if pos > 0 else np.nan
    bear = np.nanmean(e[R["bench"].values <= np.quantile(R["bench"].values, 1/3)])
    mid = len(e) // 2
    return dict(gross=e.mean(), net=net, ic=R["ic"].mean(skipna=True), b2=b2, conc5=conc5,
                bear=bear, h1=e[:mid].mean(), h2=e[mid:].mean(), turn=turn, cap=R["cap"].median(), n=len(e))


def sweep(df, cost):
    dates = np.sort(df["date"].unique()); samp = dates[252::STEP]
    out = {}
    for s in SIGS:
        rows = []; prev = None
        for i, t in enumerate(samp):
            d = df[df["date"] == t].dropna(subset=["fwd", s, "advM"])
            if len(d) < 25:
                continue
            sel = d[d[s] >= d[s].quantile(DEC)]
            cur = set(sel["ticker"]); turn = 1 - len(cur & prev) / len(cur | prev) if prev else np.nan; prev = cur
            rows.append((i, float(sel["fwd"].mean() - d["fwd"].mean()), turn, float(d["fwd"].mean()),
                         float(sel["advM"].median()), spearmanr(d[s], d["fwd"]).correlation))
        if len(rows) > 10:
            out[s] = metrics(rows, cost)
    # lgbm-combine (trailing WF, 3-seed)
    reb = [k for k in range(TRAIN_MIN, len(dates) - EMB, STEP)]
    rows = []; prev = None
    for k in reb:
        t = dates[k]; cut = dates[k - EMB]; lo = dates[max(0, k - EMB - TRAIL)]
        tr = df[(df["date"] <= cut) & (df["date"] > lo)].dropna(subset=["mn"] + SIGS)
        te = df[df["date"] == t].dropna(subset=["fwd", "advM"] + SIGS)
        if len(tr) < 3000 or len(te) < 25:
            continue
        p = np.mean([lgb.LGBMRegressor(**LGB, random_state=sd).fit(tr[SIGS], tr["mn"]).predict(te[SIGS]) for sd in SEEDS], axis=0)
        m = p >= np.quantile(p, DEC); seln = te[m]
        cur = set(seln["ticker"]); turn = 1 - len(cur & prev) / len(cur | prev) if prev else np.nan; prev = cur
        rows.append((len(rows), float(seln["fwd"].mean() - te["fwd"].mean()), turn, float(te["fwd"].mean()),
                     float(seln["advM"].median()), spearmanr(p, te["fwd"]).correlation))
    if len(rows) > 10:
        out["lgbm15"] = metrics(rows, cost)
    return out


targets = sys.argv[1:] or [u[0] for u in UNIS]
for name, path, src, cost in UNIS:
    if name not in targets:
        continue
    try:
        df = pd.read_parquet(f"var/_analysis/{path}.parquet")
    except Exception as e:
        print(f"\n### {name} [{src}] MISSING: {e}"); continue
    df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
    df = build(df); r = sweep(df, cost)
    if not r:
        print(f"\n### {name} [{src}] no folds"); continue
    print(f"\n### {name}  [src={src} cost={cost}bps tickers={df['ticker'].nunique()}]", flush=True)
    print(f"{'signal':10s} {'grossExc':>9s} {'netExc':>8s} {'rankIC':>8s} {'best-2':>8s} {'conc5':>6s} {'bear':>7s} {'H1':>7s} {'H2':>7s} {'turn':>5s} {'cap($M)':>8s}")
    ranked = sorted(r.items(), key=lambda kv: -kv[1]["net"])
    for s, m in ranked:
        robust = "  <=" if (m["net"] > 0 and m["b2"] > 0 and m["bear"] > 0 and m["h1"] > 0 and m["h2"] > 0 and (m["conc5"] != m["conc5"] or m["conc5"] < 0.7)) else ""
        print(f"{s:10s} {m['gross']*100:+8.2f}% {m['net']*100:+7.2f}% {m['ic']:+8.4f} {m['b2']*100:+7.2f}% "
              f"{(m['conc5'] if m['conc5']==m['conc5'] else 0):5.0%} {m['bear']*100:+6.2f}% {m['h1']*100:+6.2f}% "
              f"{m['h2']*100:+6.2f}% {m['turn']:4.0%} {m['cap']/1e6:7.1f}{robust}", flush=True)
print("\n  <= = passes FULL gauntlet (net+ & best-2+ & bear+ & BOTH halves+ & conc5<70%). cap=median $ADV of selected decile.", flush=True)
