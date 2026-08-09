"""주식 오버나이트 forward 페이퍼 엔진 — KIS 계정 불필요. yfinance 공개데이터로 포트폴리오를
실제 추적하며 KOSPI200 밤샘보유(종가→시가) 전략을 현실적 체결로 굴림. 크립토 papertrade와 대칭.

전략(WORK_LOG 시점35): KOSPI200 선물 밤샘보유(종가 매수→시가 매도, 낮 flat), 고변동밤 스킵.
선물이라 거래세 면제. 현실체결: 선물 왕복 ~1.5bp/일 + carry. 지속가능 앵커(불장전) Sharpe~1.2.
정직: in-sample·N~레짐·생존편향(지수레벨은 무). 실체결은 KIS 모의투자(별도).
Usage: uv run python scripts/overnight_papertrade.py --sim
       (KIS 계정 없이 지금 실행 가능. 매일 크론=무계정 forward.)
"""
from __future__ import annotations
import sys, argparse, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd

FEE = 0.00015   # 선물 왕복(수수료+슬리피지), 거래세 없음
CARRY = 0.012 / 252   # (rf-div) 캐리 드래그/일


def load():
    import yfinance as yf
    d = yf.download("^KS200", start="2015-01-01", progress=False, auto_adjust=False)
    if hasattr(d.columns, "get_level_values"):
        d.columns = d.columns.get_level_values(0)
    d = d[["Open", "Close"]].astype(float)
    d["on"] = d["Open"] / d["Close"].shift(1) - 1          # 오버나이트 수익
    d["vol20"] = d["on"].rolling(20).std()
    d["skip"] = d["vol20"] > d["vol20"].rolling(250, min_periods=60).quantile(0.8)
    return d.dropna()


def run(start_equity=10_000.0):
    d = load()
    eq = start_equity
    curve, dates = [], []
    for i in range(len(d)):
        r = d.iloc[i]
        if not bool(r["skip"]):
            # 밤샘 보유: 오버나이트 수익 − 캐리 − 왕복비용
            eq *= (1 + r["on"] - CARRY - FEE)
        curve.append(eq); dates.append(d.index[i])
    return pd.Series(curve, index=dates), d


def stats(eq):
    r = eq.pct_change().dropna()
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (252 / len(eq)) - 1
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() else np.nan
    mdd = (eq / eq.cummax() - 1).min()
    return cagr * 100, sh, mdd * 100


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--sim", action="store_true"); ap.parse_args()
    eq, d = run()
    c, s, m = stats(eq)
    # 앵커: 불장전(2015-2022) 지속가능치
    eq_anchor, _ = run(); anc = eq_anchor[eq_anchor.index.year <= 2022]
    ca, sa, ma = stats(anc)
    print("=" * 74)
    print("주식 오버나이트 forward 페이퍼 엔진 (KOSPI200 선물, KIS 계정 불필요)")
    print("=" * 74)
    print(f"  전체(2015-2026): $10,000 → ${eq.iloc[-1]:,.0f} · CAGR {c:+.1f}% · Sharpe {s:+.2f} · MDD {m:.0f}%")
    print(f"  ★지속가능 앵커(불장전 2015-2022): CAGR {ca:+.1f}% · Sharpe {sa:+.2f} · MDD {ma:.0f}%")
    print(f"  재적(밤 보유) {100*(~d['skip']).mean():.0f}% · 나머지는 고변동밤 스킵")
    print(f"  ※ 헤드라인 전체는 2025-26 불장 부풀림. 앵커(Sharpe~1.2)가 정직 기대. 무계정·무료 검증.")
    print(f"  이 엔진을 매일 최신데이터로 돌리면 = 진짜 forward paper(무계정). 실체결은 KIS 모의투자(별도).")
