"""BATCH-1 #4 — 어닝 톤/센티먼트 드리프트 (US, 즉시검증). earnings_sentiment_surprise(사전계산
ret_21d_after) + earnings_call_sentiment(11K, 2006-26 transcript finbert). 톤이 사후 드리프트를
예측하나? 단조·spread·연도부호·서프라이즈 독립성. KILL: 상위분위 21d<+0.3% 또는 서프라이즈에 흡수.
Usage: uv run python var/_analysis/test_earnings_tone.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, statsmodels.api as sm
from sqlalchemy import text
from core.db import session_scope


def decile_drift(df, pred, outs, lab):
    print(f"\n### {lab}  (n={len(df)})")
    d = df.dropna(subset=[pred]).copy()
    d["q"] = pd.qcut(d[pred].rank(method="first"), 5, labels=False)
    for o in outs:
        m = d.groupby("q")[o].mean() * 100
        spread = m.iloc[-1] - m.iloc[0]
        # HAC t on top-bottom via simple two-sample (event-level)
        hi = d[d["q"] == d["q"].max()][o].dropna(); lo = d[d["q"] == d["q"].min()][o].dropna()
        t = (hi.mean() - lo.mean()) / np.sqrt(hi.var() / len(hi) + lo.var() / len(lo))
        mono = "단조↑" if m.is_monotonic_increasing else ("단조↓" if m.is_monotonic_decreasing else "비단조")
        print(f"  {o:16s} 분위평균%: {'/'.join('%+.2f'%x for x in m)}  spread {spread:+.2f}% t={t:+.2f} [{mono}]")


with session_scope() as s:
    ess = pd.DataFrame(s.execute(text(
        "select market,ticker,filing_date,sentiment,expected_ret,surprise_21d,abs_surprise_z,"
        "ret_1d_after,ret_5d_after,ret_21d_after from earnings_sentiment_surprise")).all(),
        columns=["market", "ticker", "filing_date", "sentiment", "expected_ret", "surprise_21d",
                 "abs_surprise_z", "ret_1d_after", "ret_5d_after", "ret_21d_after"])
    ecs = pd.DataFrame(s.execute(text(
        "select market,ticker,filing_date,transcript_sent_finbert,transcript_sent_lm,word_count "
        "from earnings_call_sentiment")).all(),
        columns=["market", "ticker", "filing_date", "finbert", "lm", "word_count"])

ess["filing_date"] = pd.to_datetime(ess["filing_date"])
for c in ["sentiment", "expected_ret", "surprise_21d", "ret_1d_after", "ret_5d_after", "ret_21d_after"]:
    ess[c] = pd.to_numeric(ess[c], errors="coerce")
print("=" * 68)
print("어닝 톤 드리프트 테스트 (US)")
print("=" * 68)
# surprise_21d가 예측자인지 결과인지 진단
corr = ess[["surprise_21d", "ret_21d_after"]].corr().iloc[0, 1]
print(f"진단: corr(surprise_21d, ret_21d_after)={corr:+.2f} → {'결과(룩어헤드, 예측자 아님)' if abs(corr)>0.8 else '독립 예측자 가능'}")

decile_drift(ess, "sentiment", ["ret_1d_after", "ret_5d_after", "ret_21d_after"], "(A) sentiment → 사후수익 [precomputed 1538]")

# FM: sentiment가 서프라이즈 통제 후 독립적인가 (surprise가 예측자일 때만 의미)
d = ess.dropna(subset=["sentiment", "ret_21d_after"]).copy()
X = sm.add_constant(pd.DataFrame({"sent": (d["sentiment"] - d["sentiment"].mean()) / d["sentiment"].std()}))
r = sm.OLS(d["ret_21d_after"].values, X).fit(cov_type="HC1")
print(f"\n  단변량 ret21 ~ sentiment: coef {r.params.iloc[1]*100:+.2f}% t={r.tvalues.iloc[1]:+.2f}")

# 연도별 부호
d["yr"] = d["filing_date"].dt.year
yr = d.groupby("yr").apply(lambda x: np.corrcoef(x["sentiment"], x["ret_21d_after"])[0, 1] if len(x) > 10 else np.nan)
print(f"  연도별 corr(sentiment,ret21): {'/'.join(f'{y}:{v:+.2f}' for y,v in yr.items() if v==v)}")

# (B) earnings_call_sentiment 20년史 — 사후수익을 daily_prices에서 계산
print("\n(B) earnings_call transcript finbert (11K, 2006-26) → daily_prices 21d 사후수익 조인 중...")
ecs["filing_date"] = pd.to_datetime(ecs["filing_date"])
ecs["finbert"] = pd.to_numeric(ecs["finbert"], errors="coerce")
ecs = ecs.dropna(subset=["finbert"])
tickers = ecs["ticker"].unique().tolist()
with session_scope() as s:
    px = pd.DataFrame(s.execute(text(
        "select ticker,trade_date,close from daily_prices where market='US' and ticker = ANY(:t)"),
        {"t": tickers}).all(), columns=["ticker", "trade_date", "close"])
px["trade_date"] = pd.to_datetime(px["trade_date"]); px["close"] = px["close"].astype(float)
px = px.sort_values(["ticker", "trade_date"])
px["fwd21"] = px.groupby("ticker")["close"].shift(-21) / px["close"] - 1
# 시장평균(당일 전종목) 초과
mkt = px.groupby("trade_date")["fwd21"].transform("mean")
px["exc21"] = px["fwd21"] - mkt
m = pd.merge_asof(ecs.sort_values("filing_date"), px[["ticker", "trade_date", "fwd21", "exc21"]].sort_values("trade_date"),
                  left_on="filing_date", right_on="trade_date", by="ticker", direction="forward",
                  tolerance=pd.Timedelta(days=5))
m = m.dropna(subset=["finbert", "fwd21"])
decile_drift(m, "finbert", ["fwd21", "exc21"], f"(B) transcript finbert → 21d raw/excess [{len(m)} events, 2006-26]")
m["yr"] = m["filing_date"].dt.year
yr = m.groupby("yr").apply(lambda x: (x[x["finbert"] >= x["finbert"].quantile(.8)]["exc21"].mean()) * 100 if len(x) > 20 else np.nan)
print(f"  연도별 상위20% 톤 21d초과%: {'/'.join(f'{y}:{v:+.1f}' for y,v in yr.items() if v==v)}")
print("\n판정: 상위분위 21d초과 유의(+)·단조·연도부호 일관이면 살림 / <0.3% or 비단조 or 서프라이즈흡수면 KILL")
