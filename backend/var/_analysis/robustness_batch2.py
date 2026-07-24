"""Verification batch 2 — multiple-testing / overfitting (audit cat 9).
#9.2 DSR on EVERY candidate (not just KR_LARGE), #9.5 FWER (Bonferroni/Holm/BH) across
candidates with a stated trial-ledger N, #9.3 universe-selection haircut, #9.8 PBO/CSCV
(does in-sample-best amihud-window stay OOS above median?).
Usage: uv run python var/_analysis/robustness_batch2.py
"""
import sys, warnings, itertools
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, statsmodels.api as sm
from scipy.stats import norm
STEP, DEC = 21, 0.9; PPY = 252 / STEP
CAND = [("KR_LARGE", "px_KR_LARGE_PYKRX", 30), ("KR_MID", "px_KR_MID_PYKRX", 40),
        ("KR_MICRO", "px_KR_MICRO_PYKRX", 120), ("KR_SMALL", "px_KR_SMALL", 60), ("US_LARGE", "px_US_LARGE", 15)]
N_LEDGER = 135   # stated family size (≈9 universes × 15 signals)


def net_series(path, cost, win=63):
    d = pd.read_parquet(f"var/_analysis/{path}.parquet")
    d["date"] = pd.to_datetime(d["date"]); d["ticker"] = d["ticker"].astype(str)
    d = d.sort_values(["ticker", "date"]); g = d.groupby("ticker", group_keys=False)
    d["dv"] = d["close"] * d["volume"]; d["_ai"] = g["close"].pct_change().abs() / d["dv"].replace(0, np.nan)
    d["S"] = g["_ai"].transform(lambda s: s.rolling(win, min_periods=win // 2).mean())
    d["fwd"] = g["close"].shift(-21) / d["close"] - 1
    dates = np.sort(d["date"].unique()); e = []
    for t in dates[252::STEP]:
        x = d[d["date"] == t].dropna(subset=["fwd", "S"])
        if len(x) < 25:
            continue
        sel = x[x["S"] >= x["S"].quantile(DEC)]
        e.append(float(sel["fwd"].mean() - x["fwd"].mean()))
    return np.array(e) - cost / 1e4


def dsr(e, N):
    T = len(e); sr = e.mean() / e.std(); g3 = pd.Series(e).skew(); g4 = pd.Series(e).kurt() + 3
    sr_std = np.sqrt((1 - g3 * sr + (g4 - 1) / 4 * sr ** 2) / (T - 1)); emc = 0.5772156649
    sr0 = sr_std * ((1 - emc) * norm.ppf(1 - 1 / N) + emc * norm.ppf(1 - 1 / (N * np.e)))
    return norm.cdf((sr - sr0) / sr_std)


print("(1) 후보별 통계 + 다중검정 (net-excess 시계열):")
print(f"{'candidate':10s} {'net/reb':>8s} {'Sharpe':>7s} {'HAC t':>7s} {'p':>7s} {'DSR(N=135)':>11s}")
rows = []
for name, path, cost in CAND:
    e = net_series(path, cost)
    hac = sm.OLS(e, np.ones(len(e))).fit(cov_type="HAC", cov_kwds={"maxlags": 2})
    rows.append((name, e, hac.tvalues[0], hac.pvalues[0]))
    print(f"{name:10s} {e.mean()*100:+7.2f}% {e.mean()/e.std()*np.sqrt(PPY):+6.2f} {hac.tvalues[0]:+6.2f} {hac.pvalues[0]:7.3f} {dsr(e, N_LEDGER):10.1%}")

# FWER across the 5 candidates
ps = np.array([r[3] for r in rows]); order = np.argsort(ps)
print(f"\n(2) FWER (후보 5개 + ledger N={N_LEDGER}):")
bonf = 0.05 / N_LEDGER
print(f"    Bonferroni 임계 p<{bonf:.4f} (N={N_LEDGER}): 통과=", [rows[i][0] for i in range(5) if ps[i] < bonf])
# Holm across the 5
holm_pass = []
for rank, i in enumerate(order):
    if ps[i] < 0.05 / (N_LEDGER - rank):
        holm_pass.append(rows[i][0])
    else:
        break
print(f"    Holm(N={N_LEDGER}): 통과=", holm_pass)
# BH-FDR across 5
bh = []
for rank, i in enumerate(order):
    if ps[i] < 0.05 * (rank + 1) / N_LEDGER:
        bh.append(rows[i][0])
print(f"    BH-FDR(N={N_LEDGER}): 통과=", bh)

print(f"\n(3) 유니버스 선택 haircut: KR_LARGE를 9유니버스 중 고른 것 = 추가 선택.")
kl = rows[0][1]
print(f"    KR_LARGE DSR: N=135 {dsr(kl,135):.1%} · N=400 {dsr(kl,400):.1%} · N=1000(보수) {dsr(kl,1000):.1%}")

print(f"\n(4) PBO/CSCV - amihud window(21/63/126)+SIZE 중 IS-best가 OOS서 중앙값 위인가:")
d = pd.read_parquet("var/_analysis/px_KR_LARGE_PYKRX.parquet")
d["date"] = pd.to_datetime(d["date"]); d["ticker"] = d["ticker"].astype(str)
d = d.sort_values(["ticker", "date"]); g = d.groupby("ticker", group_keys=False)
d["dv"] = d["close"] * d["volume"]; d["_ai"] = g["close"].pct_change().abs() / d["dv"].replace(0, np.nan)
for w in (21, 63, 126):
    d[f"I{w}"] = g["_ai"].transform(lambda s: s.rolling(w, min_periods=w // 2).mean())
d["SIZE"] = -np.log(g["dv"].transform(lambda s: s.rolling(63, min_periods=20).median()).clip(lower=1))
d["fwd"] = g["close"].shift(-21) / d["close"] - 1
dates = np.sort(d["date"].unique()); samp = dates[252::STEP]
strat = ["I21", "I63", "I126", "SIZE"]
M = {s: [] for s in strat}
for t in samp:
    x = d[d["date"] == t].dropna(subset=["fwd"] + strat)
    if len(x) < 25:
        continue
    for s in strat:
        sel = x[x[s] >= x[s].quantile(DEC)]
        M[s].append(float(sel["fwd"].mean() - x["fwd"].mean()))
M = pd.DataFrame(M); S = 8; blocks = np.array_split(range(len(M)), S); nlogit = []
for combo in itertools.combinations(range(S), S // 2):
    isb = np.concatenate([blocks[i] for i in combo]); oos = np.concatenate([blocks[i] for i in range(S) if i not in combo])
    is_best = M.iloc[isb].mean().idxmax(); oos_rank = M.iloc[oos].mean().rank().loc[is_best] / len(strat)
    nlogit.append(1 if oos_rank <= 0.5 else 0)
print(f"    PBO(IS-best가 OOS 중앙값 이하 확률) = {np.mean(nlogit):.0%}  ({'낮음=견고' if np.mean(nlogit) < 0.3 else '높음=과적합위험'})")
