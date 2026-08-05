"""오버나이트 전략 forward-paper 러너 (시점35의 유일한 실현 엣지).

전략: KODEX 200 ETF(069500)를 밤에 보유 — 장 마감 직전 매수, 다음날 개장 직후 매도.
근거: KR 주식수익은 전부 오버나이트(전일종가→시가)에 발생하고 인트라데이는 -8.8%(WORK_LOG 시점35).
       개별주식은 매도세 0.15%가 죽이지만 ETF/선물은 거래세 면제 → realize 가능.
리스크관리: 20일 오버나이트 변동성 상위 20% 밤은 스킵(백테스트서 MDD -23%→-11%, Sharpe 1.38→1.56).
정직: backtest 기준 지속가능 Sharpe~1.2/CAGR~12-15%(불장전 앵커). 밤 갭 리스크(가끔 -5~-9%) 보상.
      이 러너의 목적 = in-sample을 벗어난 실체결 forward 검증. ETF는 net Sharpe 0.3~0.8(비용),
      통과 시 선물(net Sharpe 1.2~1.5)로 업그레이드.

모드:
  --sim [N]   최근 N거래일 live 가격(yfinance)으로 전략 시뮬 + 저널 + 통계 (키 불필요, 지금 실행 가능)
  --signal    오늘 밤 진입 여부(변동성 필터) 판정 출력
  --enter     장 마감 직전: KODEX200 시장가 매수 (KIS 모의투자, 키 필요)
  --exit      개장 직후: 포지션 청산 (KIS 모의투자, 키 필요)

KIS 키 세팅(무료): KIS 계좌 개설 → apiportal.koreainvestment.com OpenAPI 신청 → 모의투자 계좌 →
  .env에 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO. 그다음 스케줄러 2잡(15:15 enter / 09:01 exit).

Usage: uv run python -m scripts.overnight_paper --sim 250
"""
from __future__ import annotations

import argparse
import sys
import warnings

warnings.filterwarnings("ignore")

TICKER = "069500"          # KODEX 200 (KOSPI200 추종 ETF, 거래세 면제)
YF = "069500.KS"
VOL_SKIP_Q = 0.80          # 20일 오버나이트 변동성 이 분위 초과면 밤 스킵
ETF_COST_BP = 3.5          # 왕복 추정(스프레드+수수료, 거래세 면제)
TARGET_NOTIONAL = 10_000_000.0   # 모의 1회 명목(원). 계좌잔고 있으면 그걸로 대체


def _load(days: int = 400):
    import yfinance as yf
    import pandas as pd
    d = yf.download(YF, period=f"{max(days + 40, 120)}d", progress=False, auto_adjust=False)
    if hasattr(d.columns, "get_level_values"):
        d.columns = d.columns.get_level_values(0)
    d = d[["Open", "Close"]].astype(float)
    d["prevclose"] = d["Close"].shift(1)
    d["on"] = d["Open"] / d["prevclose"] - 1             # 오버나이트 수익
    d["vol20"] = d["on"].rolling(20).std()
    d["skip"] = d["vol20"] > d["vol20"].rolling(250, min_periods=60).quantile(VOL_SKIP_Q)
    return d.dropna()


def sim(days: int):
    import numpy as np, pandas as pd
    d = _load(days).tail(days)
    held = d["on"].where(~d["skip"], 0.0) - (~d["skip"]).astype(float) * ETF_COST_BP / 1e4
    eq = (1 + held).cumprod()
    n = len(held); mu, sd = held.mean(), held.std()
    cagr = (1 + held).prod() ** (252 / n) - 1
    shp = mu / sd * np.sqrt(252) if sd > 0 else 0
    mdd = (eq / eq.cummax() - 1).min()
    traded = held[~d["skip"]]
    print(f"\n[SIM] KODEX200 오버나이트 (최근 {n}거래일, 비용 {ETF_COST_BP}bp, 고변동밤 스킵)")
    print(f"  net CAGR {cagr*100:+.1f}%  Sharpe {shp:+.2f}  MDD {mdd*100:.1f}%  "
          f"진입율 {(~d['skip']).mean()*100:.0f}%  적중 {(traded>0).mean()*100:.0f}%")
    print(f"  누적수익 {(eq.iloc[-1]-1)*100:+.1f}%  (동일기간 buy&hold {((d['Close'].iloc[-1]/d['Close'].iloc[0])-1)*100:+.1f}%)")
    print(f"\n  최근 12거래일 저널:")
    print(f"  {'날짜':12s} {'전일종가':>10s} {'시가':>10s} {'밤수익':>8s} {'액션':>8s}")
    for dt, r in d.tail(12).iterrows():
        act = "스킵(고변동)" if r["skip"] else "보유"
        print(f"  {str(dt.date()):12s} {r['prevclose']:>10.0f} {r['Open']:>10.0f} {r['on']*100:>+7.2f}% {act:>8s}")
    print(f"\n  ※ backtest 앵커(2015-2022) Sharpe~1.2/CAGR~12-15%. forward가 이에 수렴하는지 추적.")


def _kis():
    from core.config import get_settings
    s = get_settings()
    if not getattr(s, "kis_app_key", None):
        return None
    from brokers.kis import KISBroker
    return KISBroker()


def signal_today():
    d = _load(60)
    last = d.iloc[-1]
    skip = bool(last["skip"])
    print(f"[SIGNAL] 오늘 밤 {'스킵(고변동 필터)' if skip else '★진입(KODEX200 매수 보유)'}  "
          f"(20d 변동성 {last['vol20']*100:.2f}%)")
    return not skip


def enter():
    if not signal_today():
        print("  → 필터로 오늘 밤 진입 안 함."); return
    from brokers.base import OrderIntent, OrderSide, OrderType
    from core.types import Market
    import yfinance as yf
    px = float(yf.download(YF, period="2d", progress=False, auto_adjust=False)["Close"].iloc[-1])
    br = _kis()
    vol = int(TARGET_NOTIONAL // px)
    intent = OrderIntent(market=Market.KR, ticker=TICKER, side=OrderSide.BUY,
                         order_type=OrderType.MARKET, volume=vol, comment="overnight-enter")
    if br is None:
        print(f"  [DRY] KIS 키 없음 → 실행 시: BUY {TICKER} x{vol} (@~{px:.0f}, 명목 {vol*px:,.0f}원)")
        return
    res = br.execute(intent)
    print(f"  [KIS] BUY {TICKER} x{vol}: ok={res.ok} fill={res.fill_price} err={res.error}")


def exit_():
    from core.types import Market
    br = _kis()
    if br is None:
        print(f"  [DRY] KIS 키 없음 → 실행 시: SELL(청산) {TICKER} 전량"); return
    res = br.close_position(Market.KR, TICKER, comment="overnight-exit")
    print(f"  [KIS] EXIT {TICKER}: ok={res.ok} fill={res.fill_price} err={res.error}")


if __name__ == "__main__":
    sys.path.insert(0, ".")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", nargs="?", const=250, type=int)
    ap.add_argument("--signal", action="store_true")
    ap.add_argument("--enter", action="store_true")
    ap.add_argument("--exit", action="store_true")
    a = ap.parse_args()
    if a.sim is not None:
        sim(a.sim)
    elif a.signal:
        signal_today()
    elif a.enter:
        enter()
    elif a.exit:
        exit_()
    else:
        ap.print_help()
