"""D — (1) combined turnover-aware blend of the passing low-turnover signals vs single
ILLIQ63; (2) DEFLATED SHARPE RATIO (Bailey & Lopez de Prado) on the KR_LARGE ILLIQ63
sleeve, haircutting for the ~N trials run this session (multiple-testing). DSR>0.95 =>
survives the haircut. Reported for a range of N (trial-count is uncertain).
Usage: uv run python var/_analysis/specialize_D.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy.stats import norm

UNIS = [("KR_LARGE", "px_KR_LARGE_PYKRX", 30), ("KR_MID", "px_KR_MID_PYKRX", 40), ("KR_MICRO", "px_KR_MICRO_PYKRX", 120)]
STEP, DEC = 21, 0.9; PPY = 252 / STEP
BLEND = ["ILLIQ63", "ILLIQ126", "SIZE"]   # the low-turnover passers


def prep(path):
    df = pd.read_parquet(f"var/_analysis/{path}.parquet")
    df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
    df = df.sort_values(["ticker", "date"]); g = df.groupby("ticker", group_keys=False)
    df["dv"] = (df["close"] * df["volume"]).astype(float)
    df["_ai"] = g["close"].pct_change().abs() / df["dv"].replace(0, np.nan)
    df["ILLIQ63"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
    df["ILLIQ126"] = g["_ai"].transform(lambda s: s.rolling(126, min_periods=63).mean())
    df["SIZE"] = -np.log(g["dv"].transform(lambda s: s.rolling(63, min_periods=20).median()).clip(lower=1))
    df["fwd"] = g["close"].shift(-21) / df["close"] - 1
    return df


def series(df, mode):
    dates = np.sort(df["date"].unique()); samp = dates[252::STEP]
    exc, turns = [], []; prev = None
    for t in samp:
        d = df[df["date"] == t].dropna(subset=["fwd"] + BLEND)
        if len(d) < 25:
            continue
        if mode == "ILLIQ63":
            sc = d["ILLIQ63"]
        else:  # blend = mean of cross-sectional ranks
            sc = sum(d[b].rank(pct=True) for b in BLEND) / len(BLEND)
        sel = d[sc >= sc.quantile(DEC)]
        exc.append(float(sel["fwd"].mean() - d["fwd"].mean()))
        cur = set(sel["ticker"]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else 1.0); prev = cur
    return np.array(exc), np.mean(turns)


def deflated_sharpe(r, N):
    T = len(r); sr = r.mean() / r.std()
    g3 = pd.Series(r).skew(); g4 = pd.Series(r).kurt() + 3
    sr_std = np.sqrt((1 - g3 * sr + (g4 - 1) / 4 * sr ** 2) / (T - 1))
    emc = 0.5772156649
    sr0 = sr_std * ((1 - emc) * norm.ppf(1 - 1 / N) + emc * norm.ppf(1 - 1 / (N * np.e)))  # expected max under null
    dsr = norm.cdf((sr - sr0) / sr_std)
    return sr * np.sqrt(PPY), sr0 * np.sqrt(PPY), dsr


print("=== D1: combined blend (ILLIQ63+ILLIQ126+SIZE rank-avg) vs single ILLIQ63, net-of-cost ===")
print(f"{'universe':10s} {'ILLIQ63 net':>12s} {'blend net':>11s} {'ILLIQ63 turn':>13s} {'blend turn':>11s}")
kr_large_illiq = None
for name, path, cost in UNIS:
    df = prep(path)
    ei, ti = series(df, "ILLIQ63"); eb, tb = series(df, "blend")
    ni = ei.mean() - ti * 2 * cost / 1e4; nb = eb.mean() - tb * 2 * cost / 1e4
    print(f"{name:10s} {ni*100:+11.2f}% {nb*100:+10.2f}% {ti:12.0%} {tb:10.0%}")
    if name == "KR_LARGE":
        kr_large_illiq = (ei, ti, cost)

print("\n=== D2: DEFLATED SHARPE — KR_LARGE ILLIQ63 alpha (excess), haircut for N trials ===")
ei, ti, cost = kr_large_illiq
net_series = ei - ti * 2 * cost / 1e4
sr_ann = net_series.mean() / net_series.std() * np.sqrt(PPY)
print(f"  KR_LARGE ILLIQ63 alpha: {len(net_series)} periods, ann Sharpe(net) = {sr_ann:+.2f}, "
      f"skew {pd.Series(net_series).skew():+.2f}, exkurt {pd.Series(net_series).kurt():+.2f}")
print(f"  {'N trials':>9s} {'obs Sharpe':>11s} {'null max SR':>12s} {'DSR (P true>0)':>15s} {'verdict':>10s}")
for N in [1, 20, 50, 100, 200, 400]:
    sa, s0, dsr = deflated_sharpe(net_series, N)
    print(f"  {N:9d} {sa:+10.2f} {s0:+11.2f} {dsr:14.1%} {'survives' if dsr > 0.95 else 'HAIRCUT':>10s}")
print("  DSR>0.95 => the alpha Sharpe survives the multiple-testing haircut at that trial count.")
