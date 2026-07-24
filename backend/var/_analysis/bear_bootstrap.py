"""RE-QUANTIFY the #1 biased rejection (workflow-flagged): HLRANGE (KR_LARGE +1.96%,
tied with the shipping ILLIQ63 winner) was killed on bear-tercile −0.25% — never shown
distinguishable from 0. AND the SAME bear>0 gate was WAIVED for US_LARGE ILLIQ_SN
(bear −0.56%/−0.94%) which was promoted to institutional candidate. Test: block-bootstrap
the bear-tercile mean CI for each; enforce the gate SYMMETRICALLY. If a bear number's 90%
CI crosses 0, it is not distinguishable from 0 → cannot be the sole basis to kill/waive.
Usage: uv run python var/_analysis/bear_bootstrap.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd

STEP, DEC = 21, 0.9
RNG = np.random.default_rng(0)


def load(path):
    df = pd.read_parquet(f"var/_analysis/{path}.parquet")
    df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
    df = df.sort_values(["ticker", "date"]); g = df.groupby("ticker", group_keys=False)
    df["dv"] = df["close"] * df["volume"]
    df["_ai"] = g["close"].pct_change().abs() / df["dv"].replace(0, np.nan)
    df["ILLIQ63"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
    df["ILLIQ126"] = g["_ai"].transform(lambda s: s.rolling(126, min_periods=63).mean())
    df["SIZE"] = -np.log(g["dv"].transform(lambda s: s.rolling(63, min_periods=20).median()).clip(lower=1))
    df["_hlc"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["HLRANGE"] = g["_hlc"].transform(lambda s: s.rolling(21, min_periods=10).mean())
    df["fwd"] = g["close"].shift(-21) / df["close"] - 1
    return df


def folds(df, sig, sizeneutral=False):
    src = sig.replace("_SN", "")
    dates = np.sort(df["date"].unique()); samp = dates[252::STEP]
    exc, bench = [], []
    for t in samp:
        d = df[df["date"] == t].dropna(subset=["fwd", src, "SIZE"])
        if len(d) < 25:
            continue
        s = d[src]
        if sizeneutral:
            x = d["SIZE"].values; y = s.values; b = np.polyfit(x, y, 1); s = pd.Series(y - (b[0] * x + b[1]), index=d.index)
        sel = d[s >= s.quantile(DEC)]
        exc.append(float(sel["fwd"].mean() - d["fwd"].mean())); bench.append(float(d["fwd"].mean()))
    return np.array(exc), np.array(bench)


def boot_bear(exc, bench, n=5000):
    thr = np.quantile(bench, 1/3); be = exc[bench <= thr]
    if len(be) < 3:
        return np.nan, np.nan, np.nan, len(be)
    means = [RNG.choice(be, len(be), replace=True).mean() for _ in range(n)]
    return be.mean(), np.percentile(means, 5), np.percentile(means, 95), len(be)


CASES = [
    ("KR_LARGE", "px_KR_LARGE_PYKRX", "ILLIQ63", False, "배포중 승자(ref)"),
    ("KR_LARGE", "px_KR_LARGE_PYKRX", "HLRANGE", False, "기각됨(bear −0.25)"),
    ("US_LARGE", "px_US_LARGE", "ILLIQ63_SN", True, "기관후보 승격(bear 면제?)"),
    ("US_LARGE", "px_US_LARGE", "ILLIQ126_SN", True, "기관후보 승격(bear 면제?)"),
]
print(f"{'universe':9s} {'signal':13s} {'bear mean':>10s} {'90% CI':>20s} {'nfolds':>7s}  {'0 포함?':>8s}  note")
cache = {}
for uni, path, sig, sn, note in CASES:
    if path not in cache:
        cache[path] = load(path)
    exc, bench = folds(cache[path], sig, sn)
    m, lo, hi, nb = boot_bear(exc, bench)
    cross = "YES(구별불가)" if (lo < 0 < hi) else ("neg확정" if hi < 0 else "pos확정")
    print(f"{uni:9s} {sig:13s} {m*100:+9.2f}% [{lo*100:+6.2f},{hi*100:+6.2f}]% {nb:6d}  {cross:>8s}  {note}", flush=True)
print("\n  판정: bear CI가 0을 포함하면 '음수bear'는 0과 구별불가 → 단독 기각/면제 근거로 못 씀.")
print("  → HLRANGE와 US_LARGE_SN의 bear가 둘 다 0-포함이면 게이트를 대칭 적용해야(둘 다 통과 or 둘 다 보류).")
