"""#1 CRITICAL (completeness-audit A1/#5.1) — the headline +2.13%/Sharpe 1.70 fills at the
SAME close that generates the signal = 1-bar look-ahead, worst for the illiquid decile.
Realistic execution buys at t+1 close (decide at close[t], trade next bar). If the ILLIQ
edge collapses at t+1, the finding was a look-ahead artifact. (No 'open' in caches → use
t+1 close.) KR_LARGE + US_LARGE, ILLIQ63, market-neutral excess + Sharpe.
Usage: uv run python var/_analysis/execution_lag.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
STEP, DEC = 21, 0.9; PPY = 252 / STEP
CASES = [("KR_LARGE", "px_KR_LARGE_PYKRX", 30), ("US_LARGE", "px_US_LARGE", 15),
         ("KR_MID", "px_KR_MID_PYKRX", 40), ("KR_MICRO", "px_KR_MICRO_PYKRX", 120)]


def run(name, path, cost):
    d = pd.read_parquet(f"var/_analysis/{path}.parquet")
    d["date"] = pd.to_datetime(d["date"]); d["ticker"] = d["ticker"].astype(str)
    d = d.sort_values(["ticker", "date"]); g = d.groupby("ticker", group_keys=False)
    d["dv"] = d["close"] * d["volume"]
    d["_ai"] = g["close"].pct_change().abs() / d["dv"].replace(0, np.nan)
    d["ILLIQ"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
    d["c1"] = g["close"].shift(-1)                          # t+1 close
    d["fwd0"] = g["close"].shift(-21) / d["close"] - 1      # buy at signal close (look-ahead)
    d["fwd1"] = g["close"].shift(-22) / d["c1"] - 1         # buy at t+1 close (realistic)
    dates = np.sort(d["date"].unique()); samp = dates[252::STEP]

    def series(col):
        exc, turns = [], []; prev = None
        for t in samp:
            x = d[d["date"] == t].dropna(subset=[col, "ILLIQ"])
            if len(x) < 25:
                continue
            sel = x[x["ILLIQ"] >= x["ILLIQ"].quantile(DEC)]
            exc.append(float(sel[col].mean() - x[col].mean()))
            cur = set(sel["ticker"]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else 1.0); prev = cur
        e = np.array(exc); turn = np.nanmean(turns); net = e.mean() - turn * 2 * cost / 1e4
        return net, net / (e.std()) if e.std() else np.nan, e
    n0, s0, e0 = series("fwd0"); n1, s1, e1 = series("fwd1")
    print(f"{name:9s}  t0(룩어헤드) net {n0*100:+5.2f}%/reb  Sharpe {s0*np.sqrt(PPY):+.2f}   |   "
          f"t+1(현실) net {n1*100:+5.2f}%/reb  Sharpe {s1*np.sqrt(PPY):+.2f}   |   "
          f"Δnet {(n1-n0)*100:+5.2f}%  ({(n1/n0-1)*100 if n0 else 0:+.0f}%)", flush=True)


print("체결지연 검증 — 신호close 체결(룩어헤드) vs t+1close 체결(현실):")
for name, path, cost in CASES:
    run(name, path, cost)
print("\n  판정: t+1서 net이 유지되면 룩어헤드 아님(엣지 실재). 붕괴하면 헤드라인은 1-bar 룩어헤드 아티팩트.")
