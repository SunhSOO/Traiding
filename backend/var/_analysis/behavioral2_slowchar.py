"""TEST 4 — the ILLIQ lesson generalized: slow-moving CHARACTERISTIC signals survive
cost (low turnover), fast signals (reversal/lottery, 93% turnover) don't. So hunt the
rest of the slow-moving behavioral family — low-vol / low-beta / low-idio-vol anomalies
(defensive, slow) + 52-week-high anchoring — across the efficiency gradient, judged by
NET-of-cost excess + turnover + both split-halves. Market factor = universe EW mean.

Signals (oriented IC>0 = predicts higher fwd 21d return):
  LOWVOL  = -std(ret,60)          low-volatility anomaly (defensive)
  LOWIVOL = -std(ret - EWmkt,60)  low idiosyncratic vol (lottery/MAX cousin, but slow)
  LOWBETA = -beta_to_EWmkt(60)    betting-against-beta
  HI52    = close/max(close,252)  proximity to 52-wk high (George-Hwang anchoring)
Usage: uv run python var/_analysis/behavioral2_slowchar.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd

UNIS = [("KR_MID", "px_KR_MID_PYKRX", 40), ("KR_MICRO", "px_KR_MICRO_PYKRX", 120),
        ("KR_LARGE", "px_KR_LARGE_PYKRX", 30), ("US_LARGE", "px_US_LARGE", 15)]
SIGS = ["LOWVOL", "LOWIVOL", "LOWBETA", "HI52"]
STEP, DEC = 21, 0.9


def prep(df):
    df = df.sort_values(["date", "ticker"]).copy()
    df["ret1"] = df.groupby("ticker")["close"].pct_change()
    df["mkt"] = df.groupby("date")["ret1"].transform("mean")            # EW market proxy
    df = df.sort_values(["ticker", "date"])
    g = df.groupby("ticker", group_keys=False)
    df["LOWVOL"] = -g["ret1"].transform(lambda s: s.rolling(60, min_periods=30).std())
    df["resid"] = df["ret1"] - df["mkt"]
    df["LOWIVOL"] = -g["resid"].transform(lambda s: s.rolling(60, min_periods=30).std())
    def beta(x):
        cov = x["ret1"].rolling(60, min_periods=30).cov(x["mkt"])
        var = x["mkt"].rolling(60, min_periods=30).var()
        return -(cov / (var + 1e-12))
    df["LOWBETA"] = g.apply(beta).reset_index(level=0, drop=True)
    df["hi252"] = g["close"].transform(lambda s: s.rolling(252, min_periods=120).max())
    df["HI52"] = df["close"] / df["hi252"]
    df["fwd"] = g["close"].shift(-21) / df["close"] - 1
    return df


def walk(df, sig, bps):
    dates = np.sort(df["date"].unique()); samp = dates[252::STEP]
    rows = []; prev = None
    for i, t in enumerate(samp):
        d = df[df["date"] == t].dropna(subset=["fwd", sig])
        if len(d) < 25:
            continue
        sel = d[d[sig] >= d[sig].quantile(DEC)]
        exc = float(sel["fwd"].mean() - d["fwd"].mean())
        cur = set(sel["ticker"]); turn = 1 - len(cur & prev) / len(cur | prev) if prev else np.nan; prev = cur
        rows.append((i, exc, turn, float(d["fwd"].mean())))
    R = np.array(rows); mid = len(samp) // 2
    turn = np.nanmean(R[:, 2]); net = R[:, 1].mean() - (turn if turn == turn else 0) * 2 * bps / 1e4
    b2 = np.mean(np.sort(R[:, 1])[:-2]); bear = np.nanmean(R[:, 1][R[:, 3] <= np.quantile(R[:, 3], 1/3)])
    h1 = R[R[:, 0] < mid]; h2 = R[R[:, 0] >= mid]
    hn = lambda s: s[:, 1].mean() - (np.nanmean(s[:, 2]) if len(s) else 0) * 2 * bps / 1e4
    return dict(gross=R[:, 1].mean(), turn=turn, net=net, b2=b2, bear=bear, h1=hn(h1), h2=hn(h2))


print(f"{'universe':9s} {'sig':8s} {'gross':>7s} {'turn':>5s} {'net':>7s} {'best-2':>7s} {'bear':>7s} {'H1':>7s} {'H2':>7s}  verdict")
for name, path, bps in UNIS:
    df = pd.read_parquet(f"var/_analysis/{path}.parquet")
    df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
    df = prep(df)
    for sig in SIGS:
        r = walk(df, sig, bps)
        ok = r["net"] > 0 and r["b2"] > 0 and r["bear"] > 0 and r["h1"] > 0 and r["h2"] > 0
        print(f"{name:9s} {sig:8s} {r['gross']*100:+6.2f}% {r['turn']:4.0%} {r['net']*100:+6.2f}% {r['b2']*100:+6.2f}% "
              f"{r['bear']*100:+6.2f}% {r['h1']*100:+6.2f}% {r['h2']*100:+6.2f}%  {'<== ROBUST TRADEABLE' if ok else ''}", flush=True)
    print()
print(f"  cost/side: KR_MID 40 · KR_MICRO 120 · KR_LARGE 30 · US_LARGE 15. ROBUST = net+best2+bear+BOTH halves all positive.")
