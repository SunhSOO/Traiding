"""가장 자신 있는 유니버스(KR_LARGE ILLIQ63)의 실제 월별(21일 리밸런스) 수익 분포.
세 버전: equal-weight 초과(헤드라인) / ADV-weight 초과(배포판) / marcap-clean 잔차(보수 순수알파).
각각 절대 long-only(시장+초과)와 벤치마크도 함께. 원화 수입 = 자본 규모별 환산.
Usage: uv run python var/_analysis/monthly_income.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, statsmodels.api as sm
from sqlalchemy import text
from core.db import session_scope
STEP, DEC, PPY, COST = 21, 0.9, 252 / 21, 30


def load():
    px = pd.read_parquet("var/_analysis/px_KR_LARGE_PYKRX.parquet")
    px["date"] = pd.to_datetime(px["date"]); px["ticker"] = px["ticker"].astype(str).str.zfill(6)
    px = px.sort_values(["ticker", "date"]); g = px.groupby("ticker", group_keys=False)
    px["dv"] = px["close"] * px["volume"]
    px["_ai"] = g["close"].pct_change().abs() / px["dv"].replace(0, np.nan)
    px["ILLIQ"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
    px["ADV"] = g["dv"].transform(lambda s: s.rolling(63, min_periods=20).median())
    px["logDV"] = np.log(px["ADV"].clip(lower=1))
    px["fwd"] = g["close"].shift(-21) / px["close"] - 1
    codes = px["ticker"].unique().tolist()
    with session_scope() as s:
        rows = s.execute(text("SELECT ticker, concept, value, as_of_ts FROM financial_facts "
                              "WHERE market='KR' AND period_kind='A' AND concept IN ('NET_INCOME','EPS_BASIC') AND ticker = ANY(:t)"),
                         {"t": codes}).all()
    ff = pd.DataFrame(rows, columns=["ticker", "concept", "value", "as_of"])
    ff["ticker"] = ff["ticker"].astype(str).str.zfill(6); ff["value"] = pd.to_numeric(ff["value"], errors="coerce")
    ff["as_of"] = pd.to_datetime(ff["as_of"]).dt.tz_localize(None)
    w = ff.sort_values(["ticker", "as_of"]).drop_duplicates(["ticker", "concept", "as_of"], keep="last") \
         .pivot_table(index=["ticker", "as_of"], columns="concept", values="value", aggfunc="last").reset_index()
    w["shares"] = w["NET_INCOME"] / w["EPS_BASIC"].replace(0, np.nan); w = w[w["shares"] > 0].dropna(subset=["shares"])
    m = pd.merge_asof(px[["ticker", "date"]].sort_values("date"),
                      w[["ticker", "as_of", "shares"]].rename(columns={"as_of": "date"}).sort_values("date"),
                      on="date", by="ticker", direction="backward")
    px = px.merge(m[["ticker", "date", "shares"]], on=["ticker", "date"], how="left")
    px["logMC"] = np.log((px["close"] * px["shares"]).clip(lower=1))
    return px


def series(px, mode="equal"):
    """returns (excess, long_abs, bench) per-reb arrays, cost-netted on excess & long."""
    dates = np.sort(px["date"].unique()); samp = dates[252::STEP]
    exc, lon, ben = [], [], []
    for t in samp:
        need = ["fwd", "ILLIQ", "ADV"] + (["logMC"] if mode == "marcap" else [])
        d = px[px["date"] == t].dropna(subset=need).copy()
        if len(d) < 25:
            continue
        s = d["ILLIQ"].values
        if mode == "marcap":
            x = d["logMC"].values; b = np.polyfit(x, s, 1); s = s - (b[0] * x + b[1])
        d["_s"] = s; sel = d[d["_s"] >= d["_s"].quantile(DEC)]
        if mode == "adv":
            wt = sel["ADV"].values / sel["ADV"].sum()
        else:
            wt = np.ones(len(sel)) / len(sel)
        port = float((sel["fwd"].values * wt).sum()); bench = float(d["fwd"].mean())
        exc.append(port - bench); lon.append(port); ben.append(bench)
    exc = np.array(exc) - COST / 1e4; lon = np.array(lon) - COST / 1e4
    return exc, lon, np.array(ben)


def describe(e, lab):
    n = len(e); mu = e.mean(); sd = e.std(); shp = mu / sd * np.sqrt(PPY)
    ann = (1 + mu) ** PPY - 1
    pos = (e > 0).mean()
    cum = np.cumprod(1 + e); dd = (cum / np.maximum.accumulate(cum) - 1).min()
    pcts = np.percentile(e, [5, 25, 50, 75, 95])
    hac = sm.OLS(e, np.ones(n)).fit(cov_type="HAC", cov_kwds={"maxlags": 2})
    print(f"\n### {lab}  (n={n} 리밸런스≈{n}개월)")
    print(f"  월평균(net/reb)   {mu*100:+.2f}%   std {sd*100:.2f}%   annualized {ann*100:+.1f}%")
    print(f"  Sharpe(연) {shp:.2f}   HAC t {hac.tvalues[0]:.2f}   양(+)월 비율 {pos*100:.0f}%   maxDD {dd*100:.1f}%")
    print(f"  월분포 P5/P25/median/P75/P95: {'/'.join('%+.2f'%(p*100) for p in pcts)}%")
    print(f"  최악월 {e.min()*100:+.2f}%   최고월 {e.max()*100:+.2f}%")
    return mu, sd, ann


px = load()
print("=" * 70)
print("KR_LARGE ILLIQ63 — 실제 월별(21일 리밸런스) 수익 분포  [가장 자신 있는 유니버스]")
print("=" * 70)
res = {}
for mode, lab in [("equal", "① equal-weight 초과 (헤드라인)"),
                  ("adv", "② ADV-weight 초과 (배포·실현가능판)"),
                  ("marcap", "③ marcap-clean 잔차 (보수·순수알파 하한)")]:
    exc, lon, ben = series(px, mode)
    mu, sd, ann = describe(exc, lab + " — 초과(헤지 실현)")
    res[mode] = (mu, sd, ann)
    if mode == "equal":
        # long-only 절대(시장+초과)와 벤치마크도 한 번만
        print(f"    [참고] long-only 절대 decile 월평균 {lon.mean()*100:+.2f}% (=시장 {ben.mean()*100:+.2f}% + 초과), 시장리스크 부담")

print("\n" + "=" * 70)
print("원화 수입 환산 — ② ADV-weight 초과(배포판) 기준, 월평균 %를 자본에 곱함")
print("=" * 70)
mu_adv = res["adv"][0]; sd_adv = res["adv"][1]
print(f"{'자본':>12s} {'월평균 초과수입':>16s} {'±1σ 범위(월)':>22s}")
for cap in [1e7, 5e7, 1e8, 5e8, 1e9]:
    lo = (mu_adv - sd_adv) * cap; hi = (mu_adv + sd_adv) * cap
    print(f"{cap/1e8:>9.2f}억 {mu_adv*cap/1e4:>13,.0f}만원 {'':>3s} [{lo/1e4:+,.0f} ~ {hi/1e4:+,.0f}]만원")

print("\n" + "=" * 70)
print("정직 haircut 시나리오 — forward 실현 기대(월평균 초과%)")
print("=" * 70)
base = res["equal"][0] * 100; pure = res["marcap"][0] * 100
print(f"  A) in-sample 기계적(헤드라인)      {base:+.2f}%/월   ← 백테스트가 말하는 값(상한)")
print(f"  B) 순수알파 하한(marcap-clean)     {pure:+.2f}%/월   ← size 제거 후 남는 것(유의미달 t=1.48)")
print(f"  C) OOS-collapse 이력 반영(과거 in→OOS 반토막~소멸 패턴)")
print(f"     → 낙관 {base*0.5:+.2f} / 중립 {pure:+.2f} / 비관 0.00 ~ 소폭음  (forward paper 전엔 미지)")
