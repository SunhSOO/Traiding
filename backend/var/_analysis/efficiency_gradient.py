"""EFFICIENCY-GRADIENT TEST — the decisive test of the universe-specificity thesis.

Thesis (user, 2026-07-22): looking for ONE alpha universal across all universes was
misconceived. Real behavioral/microstructure alphas should STRENGTHEN monotonically
down the market-efficiency gradient (US-large = most arbitraged → KR-micro/TW = retail-
dominated, least arbitraged). If a signal's IC grows down the gradient, that is (a)
evidence the universe-specific framing is right, and (b) a hard-to-fake signature of a
REAL mechanism — not a cross-universe-consistency failure to be rejected.

Signals (each oriented so IC>0 = signal predicts fwd 21d return; mechanism in comment):
  STR    = -ret_5d          short-term reversal   (retail overreaction → reversal premium)
  REV_1M = -ret_21d         1-month reversal       (same, longer)
  MOM    = mom_12_1         12-1 momentum          (slow info diffusion → underreaction)
  ILLIQ  = amihud_21        Amihud illiquidity     (illiquidity risk premium)
  LOTTO  = -max_1d_1m       avoid lottery stocks   (retail lottery-demand → low-MAX premium)

No ML, price-only, cross-sectional rank-IC over monthly cross-sections. Light.
Usage: uv run python var/_analysis/efficiency_gradient.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy.stats import spearmanr

# ordered MOST→LEAST efficient (retail share rises down the list)
UNIS = [
    ("US_LARGE", "px_US_LARGE"), ("US_MID", "px_US_MID"), ("US_SMALL", "px_US_SMALL"),
    ("KR_LARGE", "px_KR_LARGE_PYKRX"), ("KR_MID", "px_KR_MID_PYKRX"), ("KR_MICRO", "px_KR_MICRO_PYKRX"),
    ("TW_SMALL", "px_TW_SMALL"),
]
SIGS = ["STR", "REV_1M", "MOM", "ILLIQ", "LOTTO"]
STEP = 21


def signals(df):
    df = df.sort_values(["ticker", "date"]).copy()
    g = df.groupby("ticker", group_keys=False)
    c = df["close"]
    df["ret1"] = g["close"].pct_change()
    df["ret5"] = g["close"].pct_change(5)
    df["ret21"] = g["close"].pct_change(21)
    df["c21"] = g["close"].shift(21); df["c252"] = g["close"].shift(252)
    df["mom"] = df["c21"] / df["c252"] - 1
    dv = (c * df["volume"]).replace(0, np.nan)
    df["ai1"] = (df["ret1"].abs() / dv)
    df["ILLIQ"] = g["ai1"].transform(lambda s: s.rolling(21, min_periods=10).mean())
    df["LOTTO"] = -g["ret1"].transform(lambda s: s.rolling(21, min_periods=10).max())
    df["STR"] = -df["ret5"]; df["REV_1M"] = -df["ret21"]; df["MOM"] = df["mom"]
    df["fwd"] = g["close"].shift(-21) / df["close"] - 1
    return df


def run(path):
    df = pd.read_parquet(f"var/_analysis/{path}.parquet")
    df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
    df = signals(df)
    dates = np.sort(df["date"].unique())
    samp = dates[252::STEP]  # skip first year (need 252d history), monthly
    out = {s: [] for s in SIGS}
    for t in samp:
        d = df[df["date"] == t].dropna(subset=["fwd"])
        if len(d) < 20:
            continue
        for s in SIGS:
            sub = d.dropna(subset=[s])
            if len(sub) < 20:
                continue
            ic = spearmanr(sub[s], sub["fwd"], nan_policy="omit").correlation
            if ic == ic:
                out[s].append(ic)
    return {s: (np.mean(v), np.mean(np.array(v) > 0) if v else np.nan, len(v)) for s, v in out.items()}


print(f"{'universe':10s} " + " ".join(f"{s:>16s}" for s in SIGS))
print(f"{'(eff→)':10s} " + " ".join(f"{'IC  (hit%)':>16s}" for s in SIGS))
res = {}
for name, path in UNIS:
    r = run(path)
    res[name] = r
    cells = []
    for s in SIGS:
        ic, hit, n = r[s]
        mark = "*" if (ic == ic and abs(ic) > 0.02) else " "
        cells.append(f"{ic:+.3f}({hit:3.0%}){mark}" if ic == ic else f"{'--':>16s}")
    print(f"{name:10s} " + " ".join(f"{c:>16s}" for c in cells), flush=True)
print("\ngradient check — does |IC| grow MOST→LEAST efficient? (monotone down each column = thesis confirmed)")
for s in SIGS:
    ics = [res[n][s][0] for n, _ in UNIS]
    trend = np.polyfit(range(len(ics)), [abs(x) if x == x else 0 for x in ics], 1)[0]
    print(f"  {s:8s}: " + " → ".join(f"{x:+.3f}" for x in ics) + f"   slope={trend:+.4f} {'↑GRADIENT' if trend > 0.002 else ''}")
