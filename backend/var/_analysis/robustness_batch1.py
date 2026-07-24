"""Verification batch 1 — signal/confound/backtest robustness for KR_LARGE ILLIQ63 (headline).
Checks: #4.13 joint Fama-MacBeth (ILLIQ marginal-t after size+vol+reversal simultaneously),
#4.7 median/log amihud, #4.6 1/price confound, #4.10 vol residualization, #4.15 decile
sweep, #10.7 rebalance-phase robustness, #10.9 ex-COVID, #10.8 year-by-year.
Usage: uv run python var/_analysis/robustness_batch1.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, statsmodels.api as sm
STEP, DEC, COST = 21, 0.9, 30

d = pd.read_parquet("var/_analysis/px_KR_LARGE_PYKRX.parquet")
d["date"] = pd.to_datetime(d["date"]); d["ticker"] = d["ticker"].astype(str)
d = d.sort_values(["ticker", "date"]); g = d.groupby("ticker", group_keys=False)
d["dv"] = d["close"] * d["volume"]; d["ret1"] = g["close"].pct_change()
d["_ai"] = d["ret1"].abs() / d["dv"].replace(0, np.nan)
d["ILLIQ"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
d["ILLIQ_med"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).median())
d["ILLIQ_log"] = g["_ai"].transform(lambda s: np.log(s.clip(lower=1e-12)).rolling(63, min_periods=32).mean())
d["SIZE"] = -np.log(g["dv"].transform(lambda s: s.rolling(63, min_periods=20).median()).clip(lower=1))
d["VOL"] = g["ret1"].transform(lambda s: s.rolling(60, min_periods=30).std())
d["INVPX"] = -np.log(d["close"].clip(lower=1))
d["R5"] = -g["close"].pct_change(5); d["R21"] = -g["close"].pct_change(21)
d["fwd"] = g["close"].shift(-21) / d["close"] - 1
dates = np.sort(d["date"].unique())


def tilt(col, samp=None, resid=None, dec=DEC):
    samp = dates[252::STEP] if samp is None else samp
    e = []
    for t in samp:
        x = d[d["date"] == t].dropna(subset=["fwd", col] + (resid or []))
        if len(x) < 25:
            continue
        s = x[col].values
        if resid:
            X = sm.add_constant(x[resid].values); s = s - X @ np.linalg.lstsq(X, s, rcond=None)[0]
        s = pd.Series(s, index=x.index); sel = x[s >= s.quantile(dec)]
        e.append(float(sel["fwd"].mean() - x["fwd"].mean()))
    e = np.array(e); return e.mean() - COST / 1e4, e


def zc(s):
    return (s - s.mean()) / (s.std() + 1e-9)


# --- Fama-MacBeth: fwd ~ ILLIQ + SIZE + VOL + R5 + R21 (z-scored), ILLIQ marginal t ---
print("(1) Fama-MacBeth 다요인 — fwd ~ ILLIQ+SIZE+VOL+R5+R21 (z), ILLIQ 한계 t:")
coefs = []
for t in dates[252::STEP]:
    x = d[d["date"] == t].dropna(subset=["fwd", "ILLIQ", "SIZE", "VOL", "R5", "R21"])
    if len(x) < 30:
        continue
    X = sm.add_constant(np.column_stack([zc(x[c]) for c in ["ILLIQ", "SIZE", "VOL", "R5", "R21"]]))
    coefs.append(np.linalg.lstsq(X, x["fwd"].values, rcond=None)[0])
C = np.array(coefs); names = ["const", "ILLIQ", "SIZE", "VOL", "R5", "R21"]
for i, nm in enumerate(names):
    if nm == "const":
        continue
    m = C[:, i]; hac = sm.OLS(m, np.ones(len(m))).fit(cov_type="HAC", cov_kwds={"maxlags": 2})
    print(f"    {nm:6s} coef {m.mean()*100:+.3f}%  t={hac.tvalues[0]:+.2f}")
print("    → ILLIQ t가 SIZE와 동시통제서도 |t|>2면 독립적, ~0이면 size에 흡수됨.")

# --- confound & robustness variants ---
print("\n(2) 교란/강건 변형 (net/reb):")
base, _ = tilt("ILLIQ")
for lab, kw in [("raw ILLIQ", dict(col="ILLIQ")), ("median amihud", dict(col="ILLIQ_med")),
                ("log amihud", dict(col="ILLIQ_log")), ("1/price 중립", dict(col="ILLIQ", resid=["INVPX"])),
                ("vol 중립", dict(col="ILLIQ", resid=["VOL"])), ("size+vol+px+rev 중립", dict(col="ILLIQ", resid=["SIZE", "VOL", "INVPX", "R21"]))]:
    n, _ = tilt(**kw)
    print(f"    {lab:22s} {n*100:+.2f}%  ({'유지' if n > 0.003 else '소멸' if n < 0.001 else '약화'})")

print("\n(3) decile 컷 스윕:")
for dec in [0.8, 0.9, 0.95]:
    n, _ = tilt("ILLIQ", dec=dec)
    print(f"    top-{int((1-dec)*100)}%  net {n*100:+.2f}%")

print("\n(4) ex-COVID(2020-02~06 제외) + 리밸런스 위상 강건:")
noncov = np.array([t for t in dates[252::STEP] if not (pd.Timestamp("2020-02-01") <= t <= pd.Timestamp("2020-06-30"))])
nx, _ = tilt("ILLIQ", samp=noncov)
print(f"    ex-COVID net {nx*100:+.2f}% (vs base {base*100:+.2f}%)")
phase = [tilt("ILLIQ", samp=dates[252 + off::STEP])[0] for off in range(0, 21, 4)]
print(f"    위상 0/4/8/12/16/20 net: {['%+.2f' % (p*100) for p in phase]}  mean {np.mean(phase)*100:+.2f}±{np.std(phase)*100:.2f}%")

print("\n(5) 연도별 net:")
for yr in range(2019, 2026):
    ys = np.array([t for t in dates[252::STEP] if pd.Timestamp(t).year == yr])
    if len(ys) > 3:
        n, _ = tilt("ILLIQ", samp=ys)
        print(f"    {yr}: {n*100:+.2f}%  ({len(ys)}reb)")
