"""BATCH-2 (비평가 missed_avenue) — 오버나이트 수익 아노말리. 주식 프리미엄이 오버나이트
(전일종가→당일시가)에 집중되고 인트라데이(시가→종가)는 ~0 또는 음(-)이라는 구조적·문헌적 아노말리.
사실이면 '오버나이트만 보유'가 buy&hold 대비 같은/높은 mean을 낮은 변동으로 = mean레버. 무료 OHLC로 즉시.
대상: SPY·QQQ·EWY(지수) + KR 유동 유니버스 집계. daily_prices open/close.
Usage: uv run python var/_analysis/test_overnight.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from sqlalchemy import text
from core.db import session_scope

PPY = 252


def ann(series):
    series = series.dropna()
    if len(series) < 60:
        return None
    mu = series.mean(); sd = series.std()
    cagr = (1 + series).prod() ** (PPY / len(series)) - 1
    shp = mu / sd * np.sqrt(PPY) if sd > 0 else 0
    cum = (1 + series).cumprod(); mdd = (cum / cum.cummax() - 1).min()
    return mu, cagr, sd * np.sqrt(PPY), shp, mdd


def report(on, intr, tot, lab):
    for nm, r in [("오버나이트(종가→시가)", on), ("인트라데이(시가→종가)", intr), ("전일종가→종가(전체)", tot)]:
        a = ann(r)
        if a:
            mu, cagr, vol, shp, mdd = a
            print(f"  {lab:14s} {nm:20s} 일평균 {mu*1e4:+6.1f}bp  CAGR {cagr*100:+6.1f}%  vol {vol*100:4.1f}%  Sharpe {shp:+.2f}  MDD {mdd*100:6.1f}%")


def series_for(ticker, market):
    with session_scope() as s:
        px = pd.DataFrame(s.execute(text(
            "select trade_date,open,close from daily_prices where ticker=:t and market=:m order by trade_date"),
            {"t": ticker, "m": market}).all(), columns=["date", "open", "close"])
    px["open"] = px["open"].astype(float); px["close"] = px["close"].astype(float)
    px = px[(px["open"] > 0) & (px["close"] > 0)]
    on = px["open"] / px["close"].shift(1) - 1
    intr = px["close"] / px["open"] - 1
    tot = px["close"] / px["close"].shift(1) - 1
    return on, intr, tot


print("=" * 92)
print("오버나이트 vs 인트라데이 아노말리 — 주식 프리미엄이 밤에 몰려있나?")
print("=" * 92)
for tk, mk in [("SPY", "US"), ("QQQ", "US"), ("EWY", "MACRO")]:
    report(*series_for(tk, mk), tk)

# KR 유동 유니버스 집계 (equal-weight 일별 오버나이트/인트라데이 평균)
print("\n[KR 유동 유니버스 집계 — 종목별 오버나이트/인트라데이 평균의 등가중 포트]")
with session_scope() as s:
    codes = [r[0] for r in s.execute(text(
        "select ticker from universe_membership where market='KR'")).all()]
    px = pd.DataFrame(s.execute(text(
        "select ticker,trade_date,open,close from daily_prices where market='KR' and ticker = ANY(:t)"),
        {"t": codes}).all(), columns=["ticker", "date", "open", "close"])
px["date"] = pd.to_datetime(px["date"]); px["open"] = px["open"].astype(float); px["close"] = px["close"].astype(float)
px = px[(px["open"] > 0) & (px["close"] > 0)].sort_values(["ticker", "date"])
g = px.groupby("ticker", group_keys=False)
px["on"] = px["open"] / g["close"].shift(1) - 1
px["intr"] = px["close"] / px["open"] - 1
px["tot"] = px["close"] / g["close"].shift(1) - 1
# winsorize 극단(상장초·이상치)
for c in ["on", "intr", "tot"]:
    px[c] = px[c].clip(-0.25, 0.25)
daily = px.groupby("date")[["on", "intr", "tot"]].mean()
report(daily["on"], daily["intr"], daily["tot"], "KR유니버스")

print("\n판정: 오버나이트가 전체수익의 대부분(인트라데이≈0/음)이면 '밤보유' 전략이 같은 mean·낮은 변동 = 구조적 mean레버 후보.")
print("      단 KR/US 개인이 종가매수·시가매도로 이걸 realize할 수 있나(체결·비용·갭)는 별도 관문.")
