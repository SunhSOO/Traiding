"""BATCH-1 #3 — PEAD/SUE 단독 방향성 이벤트 스터디 (KR 배포1순위 + US). 컨센서스 없어 SUE=계절
랜덤워크: (EPS_q - EPS_{q-4})/std(Δ). 가용시점 = as_of_ts(PIT). 사후 5/21/42d raw·시장초과 드리프트를
SUE 분위별. 핵심 관문: size 위장인가 (FM excess ~ SUE + size, SUE t가 살아남나) = ILLIQ 교훈.
KILL: 상위분위 21d초과 < +0.4% or 비단조 or SUE t가 size통제 후 유의미달.
Usage: uv run python var/_analysis/test_kr_pead.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, statsmodels.api as sm
from sqlalchemy import text
from core.db import session_scope


def zc(s):
    return (s - s.mean()) / (s.std() + 1e-12)


def run(mkt):
    with session_scope() as s:
        ff = pd.DataFrame(s.execute(text(
            "select ticker,concept,period_end,value,as_of_ts from financial_facts "
            "where market=:m and period_kind='Q' and concept in ('EPS_BASIC','EPS_DILUTED','NET_INCOME')"),
            {"m": mkt}).all(), columns=["ticker", "concept", "period_end", "value", "as_of"])
    ff["ticker"] = ff["ticker"].astype(str)
    if mkt == "KR":
        ff["ticker"] = ff["ticker"].str.zfill(6)
    ff["value"] = pd.to_numeric(ff["value"], errors="coerce")
    ff["period_end"] = pd.to_datetime(ff["period_end"]); ff["as_of"] = pd.to_datetime(ff["as_of"]).dt.tz_localize(None)
    ep = ff[ff["concept"].isin(["EPS_BASIC", "EPS_DILUTED"])].sort_values("concept") \
        .drop_duplicates(["ticker", "period_end"], keep="first")  # prefer BASIC
    ni = ff[ff["concept"] == "NET_INCOME"][["ticker", "period_end", "value"]].rename(columns={"value": "ni"})
    ep = ep.merge(ni, on=["ticker", "period_end"], how="left")
    ep = ep.dropna(subset=["value"]).sort_values(["ticker", "period_end"])
    # SUE = (EPS_q - EPS_{q-4}) / rolling std of that seasonal diff
    g = ep.groupby("ticker", group_keys=False)
    ep["eps_lag4"] = g["value"].shift(4)
    ep["dseas"] = ep["value"] - ep["eps_lag4"]
    ep["sue"] = ep["dseas"] / g["dseas"].transform(lambda s: s.rolling(8, min_periods=4).std())
    ep["shares"] = (ep["ni"] / ep["value"].replace(0, np.nan)).abs()
    ev = ep.dropna(subset=["sue", "as_of"]).copy()
    ev = ev[np.isfinite(ev["sue"])]
    ev["event_date"] = ev["as_of"].dt.normalize()

    with session_scope() as s:
        px = pd.DataFrame(s.execute(text(
            "select ticker,trade_date,close from daily_prices where market=:m"), {"m": mkt}).all(),
            columns=["ticker", "trade_date", "close"])
    px["ticker"] = px["ticker"].astype(str)
    if mkt == "KR":
        px["ticker"] = px["ticker"].str.zfill(6)
    px["trade_date"] = pd.to_datetime(px["trade_date"]); px["close"] = px["close"].astype(float)
    px = px.sort_values(["ticker", "trade_date"])
    gp = px.groupby("ticker", group_keys=False)
    for h in (5, 21, 42):
        px[f"f{h}"] = gp["close"].shift(-h) / px["close"] - 1
    for h in (5, 21, 42):
        px[f"m{h}"] = px.groupby("trade_date")[f"f{h}"].transform("mean")
        px[f"e{h}"] = px[f"f{h}"] - px[f"m{h}"]
    px["mcap"] = px["close"]  # * shares merged below

    m = pd.merge_asof(ev.sort_values("event_date"),
                      px[["ticker", "trade_date", "close", "f5", "f21", "f42", "e5", "e21", "e42"]].sort_values("trade_date"),
                      left_on="event_date", right_on="trade_date", by="ticker", direction="forward",
                      tolerance=pd.Timedelta(days=6))
    m = m.dropna(subset=["sue", "f21"])
    m["mcap"] = (m["close"] * m["shares"]).replace(0, np.nan)
    m["logmc"] = np.log(m["mcap"].clip(lower=1))
    m = m[np.isfinite(m["logmc"])] if m["logmc"].notna().sum() > 100 else m

    print(f"\n{'='*66}\n### {mkt} PEAD/SUE  (events={len(m)})")
    m["q"] = pd.qcut(m["sue"].rank(method="first"), 5, labels=False)
    for o in ["f21", "e21", "f42", "e42"]:
        mm = m.groupby("q")[o].mean() * 100
        sp = mm.iloc[-1] - mm.iloc[0]
        hi = m[m["q"] == 4][o]; lo = m[m["q"] == 0][o]
        t = (hi.mean() - lo.mean()) / np.sqrt(hi.var() / len(hi) + lo.var() / len(lo))
        mono = "단조↑" if mm.is_monotonic_increasing else "비단조"
        tag = "raw" if o[0] == "f" else "excess"
        print(f"  {tag} {o[1:]:>2s}d 분위: {'/'.join('%+.2f'%x for x in mm)}  상-하 {sp:+.2f}% t={t:+.2f} [{mono}]")
    # 상위분위 절대 초과
    top = m[m["q"] == 4]
    print(f"  상위SUE분위 21d: raw {top['f21'].mean()*100:+.2f}% / excess {top['e21'].mean()*100:+.2f}%  (n={len(top)})")
    # size 위장 관문: FM excess21 ~ SUE + size
    d = m.dropna(subset=["e21", "sue", "logmc"])
    if len(d) > 200:
        X = sm.add_constant(pd.DataFrame({"sue": zc(d["sue"]), "size": zc(d["logmc"])}))
        r = sm.OLS(d["e21"].values, X).fit(cov_type="HC1")
        print(f"  [size통제] excess21 ~ SUE+size: SUE coef {r.params['sue']*100:+.3f}% t={r.tvalues['sue']:+.2f}"
              f" · size t={r.tvalues['size']:+.2f}  → SUE |t|>2면 독립(size아님)")
    # 연도별 상위분위 excess
    m["yr"] = m["event_date"].dt.year
    yr = m[m["q"] == 4].groupby("yr")["e21"].mean() * 100
    print(f"  연도별 상위SUE 21d초과%: {'/'.join(f'{y}:{v:+.1f}' for y,v in yr.items() if v==v)}")


print("=" * 66)
print("PEAD / SUE 단독 이벤트 드리프트 — 이게 진짜 신규 알파인가 size 위장인가")
for mkt in ["KR", "US"]:
    try:
        run(mkt)
    except Exception as e:
        print(f"[{mkt} 에러] {e}")
