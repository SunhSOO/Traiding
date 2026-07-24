"""Batch3b — de-confound the ADV-weight 'improvement': compare the illiquid decile to a
benchmark under the SAME weighting scheme. If the excess persists ADV-vs-ADV, the liquidity
tilt is genuine selection, not a portfolio/benchmark weighting mismatch. Also: is-the-edge-in-
the-liquid-half — split the illiquid decile at its ADV-median and test each sub-half separately.
Usage: uv run python var/_analysis/robustness_batch3b.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, statsmodels.api as sm
STEP, DEC, PPY = 21, 0.9, 252 / 21
CASES = [("KR_LARGE", "px_KR_LARGE_PYKRX", 30), ("KR_MID", "px_KR_MID_PYKRX", 40)]


def load(path):
    px = pd.read_parquet(f"var/_analysis/{path}.parquet")
    px["date"] = pd.to_datetime(px["date"]); px["ticker"] = px["ticker"].astype(str)
    px = px.sort_values(["ticker", "date"]); g = px.groupby("ticker", group_keys=False)
    px["dv"] = px["close"] * px["volume"]
    px["_ai"] = g["close"].pct_change().abs() / px["dv"].replace(0, np.nan)
    px["ILLIQ"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
    px["ADV"] = g["dv"].transform(lambda s: s.rolling(63, min_periods=20).median())
    px["fwd"] = g["close"].shift(-21) / px["close"] - 1
    return px


def stat(e, cost):
    e = np.array(e) - cost / 1e4
    hac = sm.OLS(e, np.ones(len(e))).fit(cov_type="HAC", cov_kwds={"maxlags": 2})
    return e.mean() * 100, e.mean() / e.std() * np.sqrt(PPY), hac.tvalues[0]


for uni, path, cost in CASES:
    px = load(path); dates = np.sort(px["date"].unique()); samp = dates[252::STEP]
    same_bench, liq_half, illiq_half = [], [], []
    for t in samp:
        d = px[px["date"] == t].dropna(subset=["fwd", "ILLIQ", "ADV"])
        if len(d) < 30:
            continue
        sel = d[d["ILLIQ"] >= d["ILLIQ"].quantile(DEC)]
        w = sel["ADV"].values / sel["ADV"].sum(); wb = d["ADV"].values / d["ADV"].sum()
        # ADV-weighted decile MINUS ADV-weighted universe (same scheme → clean selection excess)
        same_bench.append(float((sel["fwd"].values * w).sum() - (d["fwd"].values * wb).sum()))
        # within the illiquid decile, split by ADV-median: does edge live in the liquid or illiquid half?
        med = sel["ADV"].median(); hi = sel[sel["ADV"] >= med]; lo = sel[sel["ADV"] < med]
        liq_half.append(float(hi["fwd"].mean() - d["fwd"].mean()))
        illiq_half.append(float(lo["fwd"].mean() - d["fwd"].mean()))
    print(f"\n### {uni}")
    n, sh, tt = stat(same_bench, cost); print(f"  ADV-decile − ADV-benchmark(동일가중, clean) net {n:+.2f}%  Sharpe {sh:+.2f}  t {tt:+.2f}")
    n, sh, tt = stat(liq_half, cost); print(f"  illiquid-decile 中 유동적 절반(ADV상위) net {n:+.2f}%  Sharpe {sh:+.2f}  t {tt:+.2f}")
    n, sh, tt = stat(illiq_half, cost); print(f"  illiquid-decile 中 최소유동 절반(ADV하위) net {n:+.2f}%  Sharpe {sh:+.2f}  t {tt:+.2f}")
print("\n  판정: 동일가중 clean 초과가 양·유의면 ADV개선은 진짜 selection / 유동적절반도 양이면 최소형 비의존=실현가능")
