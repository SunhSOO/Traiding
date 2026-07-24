"""PROACTIVE SELF-AUDIT of the KR_LARGE ILLIQ63 conclusion — the checks I should have
run myself before presenting +25%/yr Sharpe 1.70, not waited to be asked:
  1. COST realism: the ILLIQ decile IS the least-liquid names → real cost >> flat 30bps.
     net alpha at 30/50/80/120/160 bps.
  2. SURVIVORSHIP: px cache = current KOSPI200 backfilled. How many names ENTERED after
     2018 (late entrants = weren't there)? And the harder bias: demoted/delisted names
     are ABSENT entirely (can't measure directly, but KOSPI200 ~10-15%/yr turnover).
  3. LONG-ONLY vs LONG-SHORT: the +25% "alpha" is excess-vs-EW = implies shorting the
     universe. KR shorting is restricted → long-only captures the long leg only.
Usage: uv run python var/_analysis/proactive_audit.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd

df = pd.read_parquet("var/_analysis/px_KR_LARGE_PYKRX.parquet")
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
df = df.sort_values(["ticker", "date"]); g = df.groupby("ticker", group_keys=False)
df["dv"] = df["close"] * df["volume"]
df["_ai"] = g["close"].pct_change().abs() / df["dv"].replace(0, np.nan)
df["ILLIQ63"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
df["fwd"] = g["close"].shift(-21) / df["close"] - 1
STEP, DEC = 21, 0.9; PPY = 252 / STEP
dates = np.sort(df["date"].unique()); samp = dates[252::STEP]

# ---- 1. cost realism: long-only top-decile absolute AND excess, turnover ----
port, bench, turns = [], [], []; prev = None
for t in samp:
    d = df[df["date"] == t].dropna(subset=["fwd", "ILLIQ63"])
    if len(d) < 25:
        continue
    sel = d[d["ILLIQ63"] >= d["ILLIQ63"].quantile(DEC)]
    port.append(sel["fwd"].mean()); bench.append(d["fwd"].mean())
    cur = set(sel["ticker"]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else 1.0); prev = cur
port = np.array(port); bench = np.array(bench); turn = np.nanmean(turns)
excess = port - bench
print(f"KR_LARGE ILLIQ63 — proactive audit ({len(port)} periods, turnover {turn:.0%}/reb)")
print("\n1) COST REALISM (illiq decile = least-liquid → real cost is HIGH):")
print(f"   {'cost/side':>9s} {'excess ann':>11s} {'long-only ann(abs−bench beta공유)':>0s}")
for c in [30, 50, 80, 120, 160]:
    net_exc = (excess.mean() - turn * 2 * c / 1e4) * PPY
    print(f"   {c:6d}bps  excess {net_exc*100:+6.1f}%/yr")
print("   ↑ flat 30bps was optimistic; the traded names are BY CONSTRUCTION the illiquid ones.")

# ---- 2. survivorship magnitude ----
first = g["date"].min() if False else df.groupby("ticker")["date"].min()
n = len(first)
late = (first > pd.Timestamp("2018-06-01")).sum()
late2 = (first > pd.Timestamp("2020-01-01")).sum()
print(f"\n2) SURVIVORSHIP (px cache = current top-200 backfilled):")
print(f"   현재 {n}종목 중 2018-06 이후 최초등장(=late 진입): {late} ({late/n:.0%})")
print(f"                  2020-01 이후 최초등장: {late2} ({late2/n:.0%})")
print(f"   + 더 큰 편향(측정불가): 2018 KOSPI200이었다가 탈락/상폐한 종목은 캐시에 아예 없음")
print(f"     (KOSPI200 회전율 ~10-15%/yr → 8년간 원년멤버 ~절반이 빠졌을 것, 전부 누락).")

# ---- 3. long-only vs long-short ----
lo_abs = port.mean() * PPY; b_abs = bench.mean() * PPY
print(f"\n3) LONG-ONLY vs LONG-SHORT:")
print(f"   excess(=롱숏 함의, KR 공매도 제약) ann {excess.mean()*PPY*100:+.1f}%")
print(f"   long-only 절대 ann {lo_abs*100:+.1f}% = 벤치 {b_abs*100:+.1f}%(시장베타) + 알파 {(lo_abs-b_abs)*100:+.1f}%")
print(f"   → 시장중립 Sharpe 1.70은 롱숏 이론치. KR 롱온리는 시장베타를 떠안음(벤치가 생존편향으로 +{b_abs*100:.0f}%/yr 부풀려짐).")
print("\n∴ 정직: +25%/yr·Sharpe1.70은 (a)비용과소(30bps) (b)생존편향(벤치+decile 둘 다) (c)롱숏가정 3중 낙관.")
print("   진짜 배포가능 알파는 이보다 상당히 낮음 — 크기는 신뢰 못 하고, 신호의 방향/구별성/유의성만 신뢰.")
