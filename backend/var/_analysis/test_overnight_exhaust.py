"""D-소진 — 오버나이트 엣지를 남김없이 짜냄. (1)양쪽 포지션(롱 오버나이트 + 숏 인트라데이, 선물로
낮에 숏=인트라데이 -8.8%도 수익화), (2)US→KR 정보흐름 신호(전일 US 등락이 KR 개장 예측?),
(3)요일 효과, (4)레버리지/사이징 vs 갭-DD, (5)비용/체결 강건성. 지속가능 앵커=불장전(2015-2022).
Usage: uv run python var/_analysis/test_overnight_exhaust.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
import yfinance as yf
PPY = 252


def flat(d):
    if hasattr(d.columns, "get_level_values"):
        d.columns = d.columns.get_level_values(0)
    return d


def st(r):
    r = pd.Series(r).dropna()
    if len(r) < 20:
        return None
    mu, sd = r.mean(), r.std()
    cum = (1 + r).cumprod()
    return dict(cagr=((1 + r).prod() ** (PPY / len(r)) - 1) * 100, shp=mu / sd * np.sqrt(PPY) if sd > 0 else 0,
                mdd=(cum / cum.cummax() - 1).min() * 100, bp=mu * 1e4)


def line(lab, r, extra=""):
    s = st(r)
    if s:
        print(f"  {lab:32s} CAGR {s['cagr']:+7.1f}%  Sharpe {s['shp']:+5.2f}  MDD {s['mdd']:6.1f}%  {extra}")


kr = flat(yf.download("^KS200", start="2015-01-01", end="2026-08-01", progress=False, auto_adjust=False))[["Open", "Close"]].astype(float)
kr["on"] = kr["Open"] / kr["Close"].shift(1) - 1
kr["intr"] = kr["Close"] / kr["Open"] - 1
kr["dow"] = kr.index.dayofweek
kr["vol20"] = kr["on"].rolling(20).std()
spy = flat(yf.download("SPY", start="2015-01-01", end="2026-08-01", progress=False, auto_adjust=False))[["Close"]].astype(float)
spy["us_ret"] = spy["Close"] / spy["Close"].shift(1) - 1
kr = kr.join(spy["us_ret"].reindex(kr.index, method="ffill"))
kr["us_prev"] = kr["us_ret"].shift(1)   # 직전 US 세션(밤새 발생) → KR 개장 예측?
CARRY = 0.012 / PPY

for lab_p, mask in [("전체 2015-2026", kr.index.year >= 2015), ("지속가능 앵커 2015-2022", kr.index.year <= 2022)]:
    d = kr[mask].dropna(subset=["on", "intr"])
    print(f"\n{'='*84}\n### {lab_p}  ({len(d)}일)\n{'='*84}")

    print("[1] 포지션 구조 (선물 1bp/거래+carry)")
    on_only = d["on"] - CARRY - 1e-4
    both = (1 + d["on"]) * (1 - d["intr"]) - 1 - 2 * CARRY - 2e-4   # 롱오버나잇+숏인트라, 왕복 2배
    short_intr = -d["intr"] - CARRY - 1e-4
    line("롱 오버나이트만", on_only)
    line("숏 인트라데이만", short_intr)
    line("양쪽(롱밤+숏낮)", both, "← 인트라 -8.8%도 수익화")
    line("[참고] buy&hold", d["Close"] / d["Close"].shift(1) - 1)

    print("[2] US→KR 정보흐름 신호")
    c = np.corrcoef(d["on"].dropna(), d["us_prev"].reindex(d["on"].dropna().index).fillna(0))[0, 1]
    print(f"  corr(KR오버나이트, 직전US수익) = {c:+.3f}")
    us_up = d["us_prev"] > 0
    line("US↑인 밤만 보유", (d["on"] - CARRY - 1e-4).where(us_up, 0.0), f"(보유율 {us_up.mean()*100:.0f}%)")
    line("US↓인 밤 스킵+양쪽", both.where(us_up, short_intr), "(US↓땐 숏만)")

    print("[3] 요일 효과 (오버나이트 CAGR)")
    dn = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금"}
    for w in range(5):
        s = st(d[d["dow"] == w]["on"])
        if s:
            print(f"    {dn[w]}요일 밤: CAGR {s['cagr']:+6.1f}%  Sharpe {s['shp']:+.2f}")

    print("[4] 필터 조합 (양쪽 기준)")
    volhi = d["vol20"] > d["vol20"].quantile(0.8)
    line("양쪽 + 고변동밤 스킵", both.where(~volhi, -d["intr"] - CARRY - 1e-4), "밤만 빼고 낮숏 유지")

    print("[5] 레버리지 sizing (롱밤+숏낮, vol타겟)")
    tv = both.rolling(20).std() * np.sqrt(PPY)
    for capL in [1, 2, 3]:
        lev = (0.15 / tv).clip(upper=capL).shift(1).fillna(1)
        line(f"vol타겟 최대 {capL}x", both * lev)

print("\n판정: 양쪽(롱밤+숏낮)이 롱만보다 유의 개선이면 인트라데이 음(-)도 수익화 = 엣지 배증.")
print("      US신호·요일·필터가 Sharpe 올리거나 갭 줄이면 채택. 지속가능 앵커(2015-22)가 진짜 기대치.")
