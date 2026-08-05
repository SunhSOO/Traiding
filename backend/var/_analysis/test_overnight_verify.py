"""D-검증 — 밤샘효과가 진짜 realize 가능한지 적대 검증. 캠페인 철칙(화려한 숫자는 검증 전 불신).
관문: (1) 감쇠 — 연도별·기간별 오버나이트가 최근 죽었나(공표 아노말리)? (2) 실거래 instrument
(KODEX200 ETF)의 정직한 net floor vs 선물 상한. (3) 선물 롤/carry/슬리피지 반영 net. (4) 갭 리스크
관리(대형 up일 후·고변동시 스킵)로 tail 줄면서 수익 유지되나. (5) 인트라데이 음(-)이 진짜 구조인가.
Usage: uv run python var/_analysis/test_overnight_verify.py
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


def stat(r):
    r = pd.Series(r).dropna()
    if len(r) < 20:
        return None
    mu, sd = r.mean(), r.std()
    return dict(bp=mu * 1e4, cagr=((1 + r).prod() ** (PPY / len(r)) - 1) * 100,
                shp=mu / sd * np.sqrt(PPY) if sd > 0 else 0,
                mdd=((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min() * 100, n=len(r))


def load(tk):
    df = flat(yf.download(tk, start="2015-01-01", end="2026-08-01", progress=False, auto_adjust=False))
    df = df[["Open", "Close"]].astype(float)
    df["on"] = df["Open"] / df["Close"].shift(1) - 1
    df["intr"] = df["Close"] / df["Open"] - 1
    df["prev_intr"] = df["intr"].shift(1)
    df["vol20"] = df["on"].rolling(20).std()
    return df.dropna()


idx = load("^KS200")   # 선물 기초 (index)
etf = load("069500.KS")  # 실거래 ETF

print("=" * 88)
print("D-검증: 밤샘효과 realize 가능성 적대 검증")
print("=" * 88)

print("\n[1] 감쇠 — 연도별 오버나이트 (^KS200 지수)")
for y, g in idx.groupby(idx.index.year):
    s = stat(g["on"])
    if s:
        print(f"  {y}: {s['bp']:+6.1f}bp/일  CAGR {s['cagr']:+6.1f}%  Sharpe {s['shp']:+5.2f}  ({s['n']}일)")

print("\n[2] 기간 3분할 — 최근에도 사나 (^KS200)")
for lab, a, b in [("2015-2018", 2015, 2018), ("2019-2022", 2019, 2022), ("2023-2026", 2023, 2026)]:
    s = stat(idx[(idx.index.year >= a) & (idx.index.year <= b)]["on"])
    print(f"  {lab}: {s['bp']:+6.1f}bp/일  CAGR {s['cagr']:+6.1f}%  Sharpe {s['shp']:+5.2f}")

print("\n[3] 실거래 floor(KODEX200 ETF) vs 선물 상한 — 비용 반영 net")
se = stat(etf["on"])
print(f"  ETF gross 오버나이트: {se['bp']:+.1f}bp/일  CAGR {se['cagr']:+.1f}%  Sharpe {se['shp']:+.2f}")
for c in [2, 3.5, 5]:
    s = stat(etf["on"] - c / 1e4)
    print(f"    ETF net(비용 {c:.1f}bp/일 왕복): CAGR {s['cagr']:+6.1f}%  Sharpe {s['shp']:+.2f}")
# 선물: 지수 오버나이트 − carry(연 ~1.2% (r-div)) − 슬리피지 − 롤
si = stat(idx["on"])
carry_daily = 0.012 / PPY   # (rf-div) ~1.2%/yr, 매 오버나이트 보유분
for slip in [0.5, 1.0, 2.0]:
    net = idx["on"] - carry_daily - slip / 1e4
    s = stat(net)
    print(f"  선물 net(슬리피지 {slip:.1f}bp+carry1.2%/yr): CAGR {s['cagr']:+6.1f}%  Sharpe {s['shp']:+.2f}  MDD {s['mdd']:.1f}%")

print("\n[4] 갭 리스크 관리 — tail 줄이면서 수익 유지되나 (^KS200, 선물 1bp+carry)")
base = idx["on"] - carry_daily - 1 / 1e4
print(f"  무필터        : {stat(base)['cagr']:+6.1f}%  Sharpe {stat(base)['shp']:+.2f}  MDD {stat(base)['mdd']:.1f}%")
# 필터A: 고변동(vol20 상위)일 밤 스킵
volhi = idx["vol20"] > idx["vol20"].quantile(0.8)
fa = base.where(~volhi, 0.0)
print(f"  고변동밤 스킵  : {stat(fa)['cagr']:+6.1f}%  Sharpe {stat(fa)['shp']:+.2f}  MDD {stat(fa)['mdd']:.1f}%")
# 필터B: 전일 인트라데이 급락(-2%↓)이면 밤 스킵(패닉 지속 회피)
fb = base.where(idx["prev_intr"] > -0.02, 0.0)
print(f"  전일급락 스킵  : {stat(fb)['cagr']:+6.1f}%  Sharpe {stat(fb)['shp']:+.2f}  MDD {stat(fb)['mdd']:.1f}%")

print("\n[5] 인트라데이 음(-) 구조 확인 (^KS200)")
print(f"  인트라데이 CAGR {stat(idx['intr'])['cagr']:+.1f}%  Sharpe {stat(idx['intr'])['shp']:+.2f}  (음(-)이면 '낮에 판다'는 구조 실재)")

print("\n판정: 연도별/기간별 감쇠 없이 최근도 Sharpe>1이고, 선물 net이 여전히 B&H(0.70)를 크게 상회하며,")
print("      갭 관리로 MDD를 낮추면서 수익 유지되면 → D는 진짜 realize 가능한 첫 엣지. 최종관문=forward paper.")
