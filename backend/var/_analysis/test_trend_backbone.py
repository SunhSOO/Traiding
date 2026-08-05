"""BATCH-1 sharpest_bet — 크로스에셋 절대모멘텀/GEM 백본 + 베타분해 + 조건부 레버리지.
비평가 주장 검증: (1) 트렌드 타이밍이 mean을 실제로 올리나, 아니면 DD만 줄이고 mean은 flat인가?
(2) '개선분'이 스킬(타이밍 alpha)인가 순전한 레버리지 베타인가? (3) KR 단독(EWY)이 박스피로 약한가?
자산: SPY(US)·EWY(KR,USD)·GLD·TLT (2015-2026, DB). rf=0 가정. 12m 절대모멘텀, 월말 신호→익월 보유.
주의: DB 10년史=대부분 불장, 위기(2008) 표본 없음 → 트렌드 위기알파 과소평가. 정직 표기.
Usage: uv run python var/_analysis/test_trend_backbone.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, statsmodels.api as sm
from sqlalchemy import text
from core.db import session_scope

ASSETS = {"SPY": "US-주식", "EWY": "KR-주식(USD)", "GLD": "금", "TLT": "장기채"}


def load():
    with session_scope() as s:
        px = pd.DataFrame(s.execute(text(
            "select ticker,trade_date,close from daily_prices where ticker = ANY(:t)"),
            {"t": list(ASSETS)}).all(), columns=["ticker", "date", "close"])
    px["date"] = pd.to_datetime(px["date"]); px["close"] = px["close"].astype(float)
    w = px.pivot_table(index="date", columns="ticker", values="close").sort_index()
    m = w.resample("ME").last()
    return m


def stats(r, lab, bench=None):
    r = r.dropna(); n = len(r); mu = r.mean(); sd = r.std()
    cagr = (1 + r).prod() ** (12 / n) - 1
    shp = mu / sd * np.sqrt(12) if sd > 0 else 0
    cum = (1 + r).cumprod(); dd = (cum / cum.cummax() - 1).min()
    pos = (r > 0).mean()
    line = f"  {lab:26s} 월평균 {mu*100:+.2f}%  CAGR {cagr*100:+5.1f}%  vol {sd*np.sqrt(12)*100:4.1f}%  Sharpe {shp:+.2f}  MDD {dd*100:5.1f}%  양월 {pos*100:.0f}%"
    if bench is not None:
        al = sm.OLS(r.values, sm.add_constant(bench.reindex(r.index).values)).fit()
        line += f"  | α {al.params[0]*12*100:+.1f}%/yr(t{al.tvalues[0]:+.1f}) β {al.params[1]:.2f}"
    print(line)
    return mu, cagr, dd


m = load()
rets = m.pct_change()
mom = m / m.shift(12) - 1        # 12m absolute momentum, known at month-end
print("=" * 78)
print(f"크로스에셋 트렌드 백본  (기간 {m.index.min().date()} ~ {m.index.max().date()}, {len(rets)-12}개월 유효)")
print("=" * 78)
print("\n[벤치마크 buy&hold]")
spy = rets["SPY"]
for a in ASSETS:
    stats(rets[a], f"B&H {a} ({ASSETS[a]})", bench=spy if a != "SPY" else None)

# 시그널: 익월 보유 위해 신호 1개월 시프트
sig = (mom > 0).shift(1)   # 절대모멘텀>0 이면 다음달 보유
print("\n[전략]")
# 1) SPY 절대모멘텀 타이밍 (SPY or 현금)
r_ts = (rets["SPY"] * sig["SPY"]).dropna()
stats(r_ts, "① SPY 절대모멘텀 타이밍", bench=spy)
# 2) GEM 듀얼모멘텀: {SPY,EWY} 중 12m 최고, 단 그 자산 모멘텀>0, else 현금
best = mom[["SPY", "EWY"]].idxmax(axis=1)
pick = best.where(mom[["SPY", "EWY"]].max(axis=1) > 0, "CASH").shift(1)
r_gem = pd.Series(0.0, index=rets.index)
for a in ["SPY", "EWY"]:
    r_gem += rets[a].where(pick == a, 0.0)
r_gem = r_gem[pick.notna()]
stats(r_gem, "② GEM 듀얼(SPY/EWY/현금)", bench=spy)
# 3) 크로스에셋 트렌드: 모멘텀>0 자산 균등, else 현금 (managed-futures-lite)
onp = sig[list(ASSETS)].astype(float)
wsum = onp.sum(axis=1).replace(0, np.nan)
r_xa = (rets[list(ASSETS)] * onp).sum(axis=1) / wsum
r_xa = r_xa.fillna(0.0)  # 전부 현금인 달 = 0
r_xa = r_xa[sig["SPY"].notna()]
stats(r_xa, "③ 크로스에셋 트렌드(4자산)", bench=spy)

# 4) 조건부 레버리지: ③에 vol-target 2x cap (borrow 0.3%/월 차감) — mean이 스킬인가 레버인가
tvol = r_xa.rolling(6).std() * np.sqrt(12)
lev = (0.15 / tvol).clip(upper=2.0).shift(1).fillna(1.0)
r_lev = (r_xa * lev) - (lev - 1).clip(lower=0) * 0.003
stats(r_lev.dropna(), "④ ③+vol타겟 레버(≤2x,차입후)", bench=spy)

print("\n판정 관문:")
print("  · 전략 mean(월평균)이 B&H SPY보다 유의하게 높은가? (아니면 DD만↓=위험도구, mean레버 아님)")
print("  · α(절편)가 유의(+)한가? β만 크면 '레버리지된 베타'이지 스킬 아님(비평가 주장).")
print("  · EWY(KR단독) B&H가 SPY보다 약하면 → KR우선 레버는 구조적 약체(크로스에셋 필수) 확인.")
print("  · ④의 mean 상승분이 ③ 대비 순전한 레버 산술이면 스킬 아님(정직 표기).")
