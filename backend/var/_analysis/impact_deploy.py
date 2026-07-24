"""CRITICAL #2 (#6.3/#6.4) — capacity and cost were two DECOUPLED numbers; "deployable
$X at +2.13%" was never established. Charge size-scaled sqrt-impact as an actual return
drag + KR sell tax (0.15%, sell-side) + spread, and draw the net-vs-AUM decay curve.
Capacity = the AUM where net-excess still clears a floor. KR_LARGE ILLIQ63 (the sleeve).
Impact model: cost_i = spread/2 + k·σ_i·sqrt(order_i / ADV_i), order_i = AUM·w_i·turnover.
Usage: uv run python var/_analysis/impact_deploy.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
STEP, DEC, FX = 21, 0.9, 1350.0; PPY = 252 / STEP
SPREAD_HALF, TAX, KIMPACT = 0.0015, 0.0015, 0.1   # 15bps half-spread, 0.15% sell tax, sqrt-impact coeff

d = pd.read_parquet("var/_analysis/px_KR_LARGE_PYKRX.parquet")
d["date"] = pd.to_datetime(d["date"]); d["ticker"] = d["ticker"].astype(str)
d = d.sort_values(["ticker", "date"]); g = d.groupby("ticker", group_keys=False)
d["dv"] = d["close"] * d["volume"]
d["_ai"] = g["close"].pct_change().abs() / d["dv"].replace(0, np.nan)
d["ILLIQ"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
d["ret1"] = g["close"].pct_change()
d["sig"] = g["ret1"].transform(lambda s: s.rolling(21, min_periods=10).std())     # daily vol
d["advU"] = g["dv"].transform(lambda s: s.rolling(21, min_periods=10).median()) / FX   # USD ADV
d["fwd"] = g["close"].shift(-21) / d["close"] - 1
dates = np.sort(d["date"].unique()); samp = dates[252::STEP]


def net_at(aum):
    exc, turns, cost = [], [], []; prev = None
    for t in samp:
        x = d[d["date"] == t].dropna(subset=["fwd", "ILLIQ", "advU", "sig"])
        if len(x) < 25:
            continue
        sel = x[x["ILLIQ"] >= x["ILLIQ"].quantile(DEC)].copy()
        w = 1.0 / len(sel)                                   # equal weight
        # per-name order size (USD) at this AUM, per rebalance (buy+sell ~ turnover)
        order = aum * w
        part = order / sel["advU"].clip(lower=1e4)           # order as fraction of daily ADV
        imp = KIMPACT * sel["sig"] * np.sqrt(part.clip(upper=5))   # sqrt-impact (cap runaway)
        c = SPREAD_HALF + imp + TAX / 2                      # half-tax approx per side (buy+sell avg)
        exc.append(float(sel["fwd"].mean() - x["fwd"].mean()))
        cur = set(sel["ticker"]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else 1.0); prev = cur
        cost.append(float((c * 2).mean()))                  # round-trip
    e = np.array(exc); turn = np.nanmean(turns); avgcost = np.nanmean(cost)
    return e.mean() - turn * avgcost, e.mean(), turn * avgcost


print("KR_LARGE ILLIQ63 — net-excess vs AUM (√-impact + spread + KR 매도세 반영):")
print(f"{'AUM':>10s} {'grossExc':>9s} {'cost drag':>10s} {'NET/reb':>9s} {'NET_ANN':>9s}")
for aum in [1e6, 5e6, 20e6, 50e6, 100e6, 300e6, 1e9]:
    net, gross, drag = net_at(aum)
    fl = "  <== 배포한계(+0.5%/reb floor)" if net < 0.005 else ""
    print(f"${aum/1e6:8.0f}M {gross*100:+8.2f}% {drag*100:9.2f}% {net*100:+8.2f}% {net*PPY*100:+8.1f}%{fl}", flush=True)
print("\n  기존 '$3.8M 배포·+2.13%'는 임팩트-비용 분리 상태였음. 위는 임팩트를 실제 drag로 차감한 진짜 곡선.")
print("  capacity = net이 +0.5%/reb 밑으로 떨어지는 AUM. (impact k=0.1 가정, 보수적일수도)")
