"""#4 — the size-neutral 'distinct illiquidity' claim (KR_LARGE/US_LARGE ILLIQ_SN passed)
may be a MECHANICAL artifact: amihud = |ret|/dollar_volume, and SIZE was −log(dollar_volume),
so neutralizing amihud on dollar-volume partly removes the signal by construction. CLEAN
test: neutralize ILLIQ on log(MARKET CAP) instead — marcap = close × shares_outstanding,
where shares = NET_INCOME/EPS_BASIC (PIT). Marcap uses shares OUTSTANDING (not traded), so
it is NOT mechanically inside amihud. If ILLIQ survives marcap-neutralization → genuinely
distinct from firm size. If it collapses like dvol-neutral → the 'distinct' claim was the
mechanical confound.
Usage: uv run python var/_analysis/size_confound.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from sqlalchemy import text
from core.db import session_scope

STEP, DEC = 21, 0.9
CASES = [("KR_LARGE", "px_KR_LARGE_PYKRX", 30, "KR"), ("US_LARGE", "px_US_LARGE", 15, "US")]


def run(uni, path, cost, mkt):
    px = pd.read_parquet(f"var/_analysis/{path}.parquet")
    px["date"] = pd.to_datetime(px["date"])
    px["ticker"] = px["ticker"].astype(str).str.zfill(6) if mkt == "KR" else px["ticker"].astype(str)
    px = px.sort_values(["ticker", "date"]); g = px.groupby("ticker", group_keys=False)
    px["dv"] = px["close"] * px["volume"]
    px["_ai"] = g["close"].pct_change().abs() / px["dv"].replace(0, np.nan)
    px["ILLIQ"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
    px["fwd"] = g["close"].shift(-21) / px["close"] - 1
    px["logDV"] = np.log(g["dv"].transform(lambda s: s.rolling(63, min_periods=20).median()).clip(lower=1))
    codes = px["ticker"].unique().tolist()
    with session_scope() as s:
        rows = s.execute(text("SELECT ticker, concept, value, as_of_ts FROM financial_facts "
                              "WHERE market=:m AND period_kind='A' AND concept IN ('NET_INCOME','EPS_BASIC') AND ticker = ANY(:t)"),
                         {"m": mkt, "t": codes}).all()
    ff = pd.DataFrame(rows, columns=["ticker", "concept", "value", "as_of"])
    ff["ticker"] = ff["ticker"].astype(str).str.zfill(6) if mkt == "KR" else ff["ticker"].astype(str)
    ff["value"] = pd.to_numeric(ff["value"], errors="coerce"); ff["as_of"] = pd.to_datetime(ff["as_of"]).dt.tz_localize(None)
    w = ff.sort_values(["ticker", "as_of"]).drop_duplicates(["ticker", "concept", "as_of"], keep="last") \
         .pivot_table(index=["ticker", "as_of"], columns="concept", values="value", aggfunc="last").reset_index()
    w["shares"] = w["NET_INCOME"] / w["EPS_BASIC"].replace(0, np.nan)
    w = w.dropna(subset=["shares"]); w = w[w["shares"] > 0]
    m = pd.merge_asof(px[["ticker", "date"]].sort_values("date"),
                      w[["ticker", "as_of", "shares"]].rename(columns={"as_of": "date"}).sort_values("date"),
                      on="date", by="ticker", direction="backward")
    px = px.merge(m[["ticker", "date", "shares"]], on=["ticker", "date"], how="left")
    px["logMC"] = np.log((px["close"] * px["shares"]).clip(lower=1))

    dates = np.sort(px["date"].unique()); samp = dates[252::STEP]

    def sweep(mode):
        rows = []; prev = None
        for t in samp:
            need = ["fwd", "ILLIQ"] + ({"dvol": ["logDV"], "marcap": ["logMC"]}.get(mode, []))
            d = px[px["date"] == t].dropna(subset=need)
            if len(d) < 25:
                continue
            s = d["ILLIQ"].values
            if mode in ("dvol", "marcap"):
                x = d["logDV" if mode == "dvol" else "logMC"].values
                b = np.polyfit(x, s, 1); s = s - (b[0] * x + b[1])
            s = pd.Series(s, index=d.index)
            sel = d[s >= s.quantile(DEC)]
            cur = set(sel["ticker"]); turn = 1 - len(cur & prev) / len(cur | prev) if prev else np.nan; prev = cur
            rows.append((float(sel["fwd"].mean() - d["fwd"].mean()), turn, float(d["fwd"].mean())))
        R = np.array(rows); turn = np.nanmean(R[:, 1]); net = R[:, 0].mean() - (turn if turn == turn else 0) * 2 * cost / 1e4
        e = R[:, 0]; mid = len(e) // 2; b2 = np.mean(np.sort(e)[:-2])
        bear = np.nanmean(e[R[:, 2] <= np.quantile(R[:, 2], 1/3)])
        return dict(net=net, b2=b2, bear=bear, h1=e[:mid].mean(), h2=e[mid:].mean(), cov=len(R))

    print(f"\n### {uni}  (shares-coverage for marcap: {px['shares'].notna().mean():.0%})")
    print(f"{'neutralize on':16s} {'net':>7s} {'best-2':>8s} {'bear':>7s} {'H1':>7s} {'H2':>7s}  판정")
    for mode, lab in [("raw", "없음(raw)"), ("dvol", "log dvol(기계적)"), ("marcap", "log marcap(깨끗)")]:
        r = sweep(mode)
        ok = r["net"] > 0 and r["b2"] > 0 and r["bear"] > 0 and r["h1"] > 0 and r["h2"] > 0
        print(f"{lab:16s} {r['net']*100:+6.2f}% {r['b2']*100:+7.2f}% {r['bear']*100:+6.2f}% "
              f"{r['h1']*100:+6.2f}% {r['h2']*100:+6.2f}%  {'통과' if ok else '실패'}", flush=True)


for uni, path, cost, mkt in CASES:
    run(uni, path, cost, mkt)
print("\n  판정: marcap-중립 ILLIQ가 통과하면 → '순수 비유동'은 진짜(size와 구별). 실패하면 → dvol-중립 통과는 기계적 교란이었음.")
