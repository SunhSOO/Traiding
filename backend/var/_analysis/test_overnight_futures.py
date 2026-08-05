"""D — KR 오버나이트 아노말리를 KOSPI200 선물/ETF로 realize 가능한가?
개별주식 롱온리는 매도세 0.15%가 12bp gross를 죽였음. 선물·국내주식ETF는 그 세금 면제.
핵심: (1) 밤샘효과가 캡가중(대형주) 지수에서도 사나 아니면 소형주 현상인가?
     (2) 선물/ETF 비용 + 오버나이트 갭 리스크 net으로 남나?
데이터: yfinance 069500.KS(KODEX200 ETF, 거래가능)·^KS200(선물기초)·^KS11(코스피) + DB 등가중/ADV가중.
Usage: uv run python var/_analysis/test_overnight_futures.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
import yfinance as yf
from sqlalchemy import text
from core.db import session_scope
PPY = 252


def flat(d):
    if hasattr(d.columns, "get_level_values"):
        d.columns = d.columns.get_level_values(0)
    return d


def ann(r):
    r = pd.Series(r).dropna()
    if len(r) < 60:
        return None
    mu, sd = r.mean(), r.std()
    cagr = (1 + r).prod() ** (PPY / len(r)) - 1
    shp = mu / sd * np.sqrt(PPY) if sd > 0 else 0
    cum = (1 + r).cumprod(); mdd = (cum / cum.cummax() - 1).min()
    return dict(mu=mu, cagr=cagr, vol=sd * np.sqrt(PPY), shp=shp, mdd=mdd)


def show(on, intr, tot, lab, cost_bp=0.0):
    for nm, r in [("오버나이트", on), ("인트라데이", intr), ("전체B&H", tot)]:
        a = ann(r)
        if a:
            print(f"  {lab:16s} {nm:9s} 일평균 {a['mu']*1e4:+6.1f}bp  CAGR {a['cagr']*100:+6.1f}%  vol {a['vol']*100:4.1f}%  Sharpe {a['shp']:+5.2f}  MDD {a['mdd']*100:6.1f}%")
    if cost_bp:
        net = ann(pd.Series(on).dropna() - cost_bp / 1e4)
        if net:
            print(f"  {lab:16s} {'└net오버나잇':9s} 일평균 {net['mu']*1e4:+6.1f}bp  CAGR {net['cagr']*100:+6.1f}%  Sharpe {net['shp']:+5.2f}  (비용 {cost_bp:.1f}bp/일 차감)")


def parts(df):
    o = df["Open"].astype(float); c = df["Close"].astype(float)
    on = o / c.shift(1) - 1          # 전일종가 → 당일시가 (오버나이트)
    intr = c / o - 1                 # 당일시가 → 당일종가 (인트라데이)
    tot = c / c.shift(1) - 1
    # open이 stale(=prevclose)인지 진단
    stale = (on.abs() < 1e-6).mean()
    return on, intr, tot, stale


print("=" * 96)
print("D. KR 오버나이트를 선물/ETF로 realize 가능한가 — 캡가중 실측 + 비용/갭 리스크")
print("=" * 96)
print("\n[1] 캡가중 지수 실측 (yfinance, 2015-2026)")
insts = [("069500.KS", "KODEX200 ETF", 5.0), ("^KS200", "KOSPI200지수", 1.5), ("^KS11", "KOSPI지수", 1.5), ("SPY", "SPY(참고)", 1.5)]
kospi_on = None
for tk, lab, cost in insts:
    try:
        df = flat(yf.download(tk, start="2015-01-01", end="2026-08-01", progress=False, auto_adjust=False))
        on, intr, tot, stale = parts(df)
        if stale > 0.3:
            print(f"  [{lab}] open이 stale({stale:.0%}가 prevclose와 동일) → 오버나이트 측정 불가, 스킵")
            continue
        show(on, intr, tot, lab, cost_bp=cost)
        if tk == "069500.KS":
            kospi_on = on.copy()
    except Exception as e:
        print(f"  [{lab}] 에러 {e}")

print("\n[2] DB 교차확인 — 등가중(소형주 포함) vs ADV가중(대형주 편중=캡 프록시)")
with session_scope() as s:
    codes = [r[0] for r in s.execute(text("select ticker from universe_membership where market='KR'")).all()]
    px = pd.DataFrame(s.execute(text(
        "select ticker,trade_date,open,close,volume from daily_prices where market='KR' and ticker = ANY(:t)"),
        {"t": codes}).all(), columns=["ticker", "date", "open", "close", "volume"])
px["date"] = pd.to_datetime(px["date"])
for c in ["open", "close", "volume"]:
    px[c] = px[c].astype(float)
px = px[(px["open"] > 0) & (px["close"] > 0)].sort_values(["ticker", "date"])
g = px.groupby("ticker", group_keys=False)
px["on"] = (px["open"] / g["close"].shift(1) - 1).clip(-0.25, 0.25)
px["dv"] = px["close"] * px["volume"]
px["adv"] = g["dv"].transform(lambda s: s.rolling(63, min_periods=20).median())
# 등가중
eq = px.groupby("date")["on"].mean()
# ADV가중 (대형/유동 편중 = 캡 프록시)
def advw(x):
    w = x["adv"]; w = w / w.sum()
    return (x["on"] * w).sum() if w.sum() > 0 else np.nan
adv = px.dropna(subset=["adv"]).groupby("date").apply(advw)
for lab, series in [("KR등가중", eq), ("KR-ADV가중(캡프록시)", adv)]:
    a = ann(series.dropna())
    if a:
        net = ann(series.dropna() - 5 / 1e4)
        print(f"  {lab:20s} 오버나잇 일평균 {a['mu']*1e4:+6.1f}bp  CAGR {a['cagr']*100:+6.1f}%  Sharpe {a['shp']:+5.2f}"
              f"  | net(5bp) CAGR {net['cagr']*100:+.1f}% Sharpe {net['shp']:+.2f}")

print("\n[3] 오버나이트 갭 리스크 (069500.KS 기준) — 밤새 뭘 감수하나")
if kospi_on is not None:
    o = kospi_on.dropna()
    print(f"  오버나이트 일수익 std {o.std()*100:.2f}%  최악5일 {sorted(o.round(4).tolist())[:5]}")
    print(f"  <-2% 갭 비율 {100*(o<-0.02).mean():.1f}%  <-3% {100*(o<-0.03).mean():.1f}%  최대 하락갭 {o.min()*100:.1f}%")
    cum = (1 + o).cumprod(); print(f"  오버나이트-only 누적 MDD {((cum/cum.cummax()-1).min())*100:.1f}%")

print("\n판정: 캡가중(KODEX200/^KS200) 오버나이트가 net(+)·Sharpe가 B&H보다 높으면 realize 가능.")
print("      ADV가중이 등가중보다 급감하면 = 소형주 현상(선물로 못 잡음). 갭 리스크가 수익 대비 크면 기각.")
