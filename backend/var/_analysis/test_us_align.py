"""US→KR 신호 무결 재검증 — Sharpe 6.75가 look-ahead인지. 캘린더 정렬을 merge_asof(엄격<)로:
KR 개장(t) 시점에 알 수 있는 US = KR날짜 t보다 '엄격히 이전' US 세션(그 세션은 KR 개장 전 마감).
원래 ffill+shift(1)이 같은날짜 US를 미리 봤을 가능성 검증. 또한 경제적 realize 불가(선물 야간장이
US를 이미 반영)임을 명시.
Usage: uv run python var/_analysis/test_us_align.py
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
    mu, sd = r.mean(), r.std()
    return dict(cagr=((1 + r).prod() ** (PPY / len(r)) - 1) * 100, shp=mu / sd * np.sqrt(PPY) if sd > 0 else 0, n=len(r))


kr = flat(yf.download("^KS200", start="2015-01-01", end="2026-08-01", progress=False, auto_adjust=False))[["Open", "Close"]].astype(float)
kr["on"] = kr["Open"] / kr["Close"].shift(1) - 1
spy = flat(yf.download("SPY", start="2015-01-01", end="2026-08-01", progress=False, auto_adjust=False))[["Close"]].astype(float)
spy["us_ret"] = spy["Close"] / spy["Close"].shift(1) - 1

krd = kr.reset_index(); krd.columns = ["kdate"] + list(krd.columns[1:])
usd = spy.reset_index(); usd.columns = ["udate"] + list(usd.columns[1:]); usd = usd[["udate", "us_ret"]].dropna()

# 방식A (원래): ffill(같은날짜 포함=잠재 look-ahead) + shift(1)
kr["A_ffill_shift"] = spy["us_ret"].reindex(kr.index, method="ffill").shift(1)
# 방식B (무결): US날짜가 KR날짜보다 '엄격히 이전'인 최신 US (그 세션은 KR 개장 전 마감 = 진짜 알 수 있음)
mB = pd.merge_asof(krd.sort_values("kdate"), usd.sort_values("udate"), left_on="kdate", right_on="udate",
                   direction="backward", allow_exact_matches=False)
kr["B_clean"] = mB.set_index("kdate")["us_ret"].reindex(kr.index)
# 방식C (명백한 look-ahead 대조군): 같은날짜 US (KR개장 후 마감 = 미래)
kr["C_lookahead"] = spy["us_ret"].reindex(kr.index, method="ffill")

for lab, mask in [("전체", kr.index.year >= 2015), ("앵커 2015-2022", kr.index.year <= 2022)]:
    d = kr[mask].dropna(subset=["on"])
    print(f"\n### {lab} ({len(d)}일)")
    for sig, nm in [("A_ffill_shift", "A 원래(ffill+shift)"), ("B_clean", "B 무결(엄격<)"), ("C_lookahead", "C 대조군(명백look-ahead)")]:
        dd = d.dropna(subset=[sig])
        c = np.corrcoef(dd["on"], dd[sig])[0, 1]
        up = dd[sig] > 0
        s_cond = st((dd["on"] - 1.2 / 100 / PPY - 1e-4).where(up, 0.0))
        print(f"  {nm:26s} corr {c:+.3f}  | US↑밤만보유: CAGR {s_cond['cagr']:+7.1f}%  Sharpe {s_cond['shp']:+5.2f} (보유 {up.mean()*100:.0f}%)")

print("\n판정: B(무결)가 A와 비슷하면 look-ahead 아님(단 경제적 realize는 별개). B가 붕괴하면 A는 데이터 누수였음.")
print("      C(대조군)는 명백 look-ahead라 Sharpe 폭등해야 정상 — A가 C에 가까우면 A도 누수.")
print("  ※ 경제적 현실: KR 오버나이트는 US세션을 포함. 선물 야간장이 US를 실시간 반영해 개장 갭 선소멸.")
print("    → US조건부는 '제때 행동 불가'라 realize 불가. 실현 엣지는 무조건부 오버나이트(선물)뿐.")
