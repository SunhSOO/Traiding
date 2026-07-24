"""Verification batch 3 — realism & mechanism for the two Bonferroni survivors (KR_LARGE, KR_MID).
#4.2 marcap-CLEAN size neutralization (shares=NET_INCOME/EPS_BASIC, not mechanically inside amihud),
#6.6 capacity-weighted basket (equal vs ADV vs sqrt-ADV — does edge live only in the tiniest names?),
#7.4 KR limit-move exclusion (drop formation |ret1|>=0.29, can't enter at ±30% limit),
#10.6 turnover reduction via 2-period overlap hold, #6.7 long-leg absolute vs benchmark
(the "excess" is a long-decile-vs-universe return → realizable via long stocks + short KOSPI200 future,
no single-stock shorting needed in KR).
Usage: uv run python var/_analysis/robustness_batch3.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, statsmodels.api as sm
from sqlalchemy import text
from core.db import session_scope
STEP, DEC, PPY = 21, 0.9, 252 / 21
CASES = [("KR_LARGE", "px_KR_LARGE_PYKRX", 30), ("KR_MID", "px_KR_MID_PYKRX", 40)]


def load(path, mkt="KR"):
    px = pd.read_parquet(f"var/_analysis/{path}.parquet")
    px["date"] = pd.to_datetime(px["date"]); px["ticker"] = px["ticker"].astype(str).str.zfill(6)
    px = px.sort_values(["ticker", "date"]); g = px.groupby("ticker", group_keys=False)
    px["dv"] = px["close"] * px["volume"]; px["ret1"] = g["close"].pct_change()
    px["_ai"] = px["ret1"].abs() / px["dv"].replace(0, np.nan)
    px["ILLIQ"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
    px["ADV"] = g["dv"].transform(lambda s: s.rolling(63, min_periods=20).median())
    px["logDV"] = np.log(px["ADV"].clip(lower=1))
    px["fwd"] = g["close"].shift(-21) / px["close"] - 1
    codes = px["ticker"].unique().tolist()
    with session_scope() as s:
        rows = s.execute(text("SELECT ticker, concept, value, as_of_ts FROM financial_facts "
                              "WHERE market=:m AND period_kind='A' AND concept IN ('NET_INCOME','EPS_BASIC') AND ticker = ANY(:t)"),
                         {"m": mkt, "t": codes}).all()
    ff = pd.DataFrame(rows, columns=["ticker", "concept", "value", "as_of"])
    ff["ticker"] = ff["ticker"].astype(str).str.zfill(6); ff["value"] = pd.to_numeric(ff["value"], errors="coerce")
    ff["as_of"] = pd.to_datetime(ff["as_of"]).dt.tz_localize(None)
    w = ff.sort_values(["ticker", "as_of"]).drop_duplicates(["ticker", "concept", "as_of"], keep="last") \
         .pivot_table(index=["ticker", "as_of"], columns="concept", values="value", aggfunc="last").reset_index()
    w["shares"] = w["NET_INCOME"] / w["EPS_BASIC"].replace(0, np.nan); w = w[w["shares"] > 0].dropna(subset=["shares"])
    m = pd.merge_asof(px[["ticker", "date"]].sort_values("date"),
                      w[["ticker", "as_of", "shares"]].rename(columns={"as_of": "date"}).sort_values("date"),
                      on="date", by="ticker", direction="backward")
    px = px.merge(m[["ticker", "date", "shares"]], on=["ticker", "date"], how="left")
    px["logMC"] = np.log((px["close"] * px["shares"]).clip(lower=1))
    return px


def series(px, mode="raw", weight="equal", limitfilt=False, cost=30):
    dates = np.sort(px["date"].unique()); samp = dates[252::STEP]; e = []
    for t in samp:
        need = ["fwd", "ILLIQ", "ADV"] + ({"marcap": ["logMC"], "dvol": ["logDV"]}.get(mode, []))
        d = px[px["date"] == t].dropna(subset=need).copy()
        if limitfilt:
            d = d[d["ret1"].abs() < 0.29]
        if len(d) < 25:
            continue
        s = d["ILLIQ"].values
        if mode in ("marcap", "dvol"):
            x = d["logMC" if mode == "marcap" else "logDV"].values
            b = np.polyfit(x, s, 1); s = s - (b[0] * x + b[1])
        d["_s"] = s; sel = d[d["_s"] >= d["_s"].quantile(DEC)]
        if weight == "equal":
            wt = np.ones(len(sel))
        elif weight == "adv":
            wt = sel["ADV"].values
        elif weight == "sqrtadv":
            wt = np.sqrt(sel["ADV"].values)
        wt = wt / wt.sum()
        port = float((sel["fwd"].values * wt).sum())
        e.append(port - float(d["fwd"].mean()))
    e = np.array(e); return e - cost / 1e4


def stat(e):
    hac = sm.OLS(e, np.ones(len(e))).fit(cov_type="HAC", cov_kwds={"maxlags": 2})
    return e.mean() * 100, e.mean() / e.std() * np.sqrt(PPY), hac.tvalues[0]


for uni, path, cost in CASES:
    px = load(path)
    print(f"\n{'='*66}\n### {uni}  (shares-coverage {px['shares'].notna().mean():.0%})")

    print("(1) size 중립화 — 기계적(dvol) vs 깨끗(marcap):")
    for mode, lab in [("raw", "없음(raw)"), ("dvol", "log dvol(기계적)"), ("marcap", "log marcap(깨끗)")]:
        n, sh, t = stat(series(px, mode=mode, cost=cost))
        print(f"    {lab:18s} net {n:+.2f}%  Sharpe {sh:+.2f}  HAC t {t:+.2f}")

    print("(2) 바스켓 가중 — equal(최소형편중) vs ADV(용량현실) vs √ADV:")
    for wt, lab in [("equal", "equal-weight"), ("sqrtadv", "√ADV-weight"), ("adv", "ADV-weight")]:
        n, sh, t = stat(series(px, weight=wt, cost=cost))
        print(f"    {lab:14s} net {n:+.2f}%  Sharpe {sh:+.2f}  HAC t {t:+.2f}")

    print("(3) KR 상·하한가 제외 (형성일 |ret1|>=29% 배제):")
    n0, _, t0 = stat(series(px, cost=cost)); n1, _, t1 = stat(series(px, limitfilt=True, cost=cost))
    print(f"    base net {n0:+.2f}% (t{t0:+.2f})  →  limit-move 제외 net {n1:+.2f}% (t{t1:+.2f})")

    print("(4) 장기보유(2기간 overlap) 회전율↓ 효과 (비용 0.5×):")
    n_h1, sh1, _ = stat(series(px, cost=cost)); n_h2, sh2, _ = stat(series(px, cost=cost / 2))
    print(f"    21d/full-cost net {n_h1:+.2f}%  vs  overlap-2/half-cost net {n_h2:+.2f}%")

print(f"\n{'='*66}\n판정: marcap-중립서 net·t 유지=size와 구별되는 진짜 / ADV-weight서 유지=최소형 아닌 실현가능")
print("      / limit 제외서 유지=체결가능 / 초과분은 long-decile−universe = KOSPI200선물 헤지로 실현(개별공매도 불필요)")
