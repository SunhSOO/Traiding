"""CRITICAL #3 (#8.2/#8.4) + #8 (#4.9) from the completeness audit:
 (A) CI on the HEADLINE net-excess & Sharpe — real stationary BLOCK bootstrap (block ≥
     holding period) + Lo(2002) Sharpe SE + Newey-West HAC t-stat. (My prior 'block
     bootstrap' was iid — audit D2.) Does +2.13% / Sharpe 1.70 have a CI excluding 0?
 (B) REVERSAL collinearity — is ILLIQ just short-term reversal in disguise? Residualize
     ILLIQ on {ret_5d, ret_21d} cross-sectionally each date, re-run the tilt. If it
     collapses, ILLIQ = reversal; if it survives, distinct.
KR_LARGE + US_LARGE, ILLIQ63.
Usage: uv run python var/_analysis/ci_reversal.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
import statsmodels.api as sm
STEP, DEC = 21, 0.9; PPY = 252 / STEP; RNG = np.random.default_rng(0)
CASES = [("KR_LARGE", "px_KR_LARGE_PYKRX", 30), ("US_LARGE", "px_US_LARGE", 15)]


def prep(path):
    d = pd.read_parquet(f"var/_analysis/{path}.parquet")
    d["date"] = pd.to_datetime(d["date"]); d["ticker"] = d["ticker"].astype(str)
    d = d.sort_values(["ticker", "date"]); g = d.groupby("ticker", group_keys=False)
    d["dv"] = d["close"] * d["volume"]
    d["_ai"] = g["close"].pct_change().abs() / d["dv"].replace(0, np.nan)
    d["ILLIQ"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
    d["r5"] = -g["close"].pct_change(5); d["r21"] = -g["close"].pct_change(21)  # (neg = reversal orientation as controls)
    d["fwd"] = g["close"].shift(-21) / d["close"] - 1
    return d


def fold_excess(d, resid_on=None):
    dates = np.sort(d["date"].unique()); samp = dates[252::STEP]; exc = []
    for t in samp:
        need = ["fwd", "ILLIQ"] + (resid_on or [])
        x = d[d["date"] == t].dropna(subset=need)
        if len(x) < 25:
            continue
        s = x["ILLIQ"].values
        if resid_on:
            X = np.column_stack([x[c].values for c in resid_on]); X = np.column_stack([np.ones(len(X)), X])
            b, *_ = np.linalg.lstsq(X, s, rcond=None); s = s - X @ b
        s = pd.Series(s, index=x.index); sel = x[s >= s.quantile(DEC)]
        exc.append(float(sel["fwd"].mean() - x["fwd"].mean()))
    return np.array(exc)


def block_boot(e, block=2, n=5000):
    T = len(e); nb = int(np.ceil(T / block)); means = []
    for _ in range(n):
        starts = RNG.integers(0, T, nb)
        samp = np.concatenate([np.take(e, range(s, s + block), mode="wrap") for s in starts])[:T]
        means.append(samp.mean())
    return np.percentile(means, 5), np.percentile(means, 95)


print("(A) 헤드라인 CI/HAC (block bootstrap + Lo SE + Newey-West):")
for name, path, cost in CASES:
    d = prep(path); e = fold_excess(d); net = e - cost / 1e4
    lo, hi = block_boot(net)
    sr = net.mean() / net.std(); T = len(net)
    lo_se = np.sqrt((1 + sr**2 / 2) / T)                     # Lo(2002) Sharpe SE (per-period)
    hac = sm.OLS(net, np.ones(T)).fit(cov_type="HAC", cov_kwds={"maxlags": 2})
    print(f"  {name:9s} net {net.mean()*100:+.2f}%/reb  block-CI[{lo*100:+.2f},{hi*100:+.2f}]  "
          f"Sharpe {sr*np.sqrt(PPY):+.2f}±{lo_se*np.sqrt(PPY):.2f}  HAC t={hac.tvalues[0]:.2f} (p={hac.pvalues[0]:.3f})", flush=True)

print("\n(B) ILLIQ이 그냥 reversal인가 — {ret5,ret21} 잔차화 후:")
for name, path, cost in CASES:
    d = prep(path)
    raw = fold_excess(d).mean() - cost / 1e4
    res = fold_excess(d, resid_on=["r5", "r21"]).mean() - cost / 1e4
    print(f"  {name:9s} raw net {raw*100:+.2f}%  →  reversal-중립 net {res*100:+.2f}%  "
          f"({'유지=distinct' if res > 0.003 else 'reversal 상당부분'})", flush=True)
print("\n  판정: HAC t>2 & block-CI가 0 제외면 유의. reversal-중립서 net 유지면 ILLIQ≠reversal.")
