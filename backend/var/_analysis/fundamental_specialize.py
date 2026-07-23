"""Fundamentals layer for KR_LARGE/MID specialization (value/quality/growth + SUE).
financial_facts is DART-covered for liquid KR only (LARGE 177/200, MID 135/300) — the
exact universes where fundamentals should matter (arbitraged less than US-large). PIT
via as_of_ts (known date). Shares derived = NET_INCOME/EPS_BASIC → per-share value
ratios without a shares column. Same gauntlet as universe_specialize (net@cost, best-2,
conc5, bear, split-half, capacity).

Signals (oriented IC>0 = predicts higher fwd 21d):
  EP=eps/px  BM=book/px  SP=sales/px  CFP=cfo/px  ROE  ROA  GPA  MARGIN
  ACCRUAL=-(ni-cfo)/assets  NIGROWTH  REVGROWTH  SUE(quarterly)
Usage: uv run python var/_analysis/fundamental_specialize.py [KR_LARGE|KR_MID]
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from sqlalchemy import text
from core.db import session_scope

UNI = sys.argv[1] if len(sys.argv) > 1 else "KR_MID"
MKT = "US" if UNI.startswith("US") else "KR"
PATH = {"KR_LARGE": "px_KR_LARGE_PYKRX", "KR_MID": "px_KR_MID_PYKRX",
        "US_LARGE": "px_US_LARGE", "US_MID": "px_US_MID", "US_SMALL": "px_US_SMALL"}[UNI]
COST = {"KR_LARGE": 30, "KR_MID": 40, "US_LARGE": 15, "US_MID": 20, "US_SMALL": 25}[UNI]
ZF = (lambda s: s.astype(str).str.zfill(6)) if MKT == "KR" else (lambda s: s.astype(str))
STEP, DEC = 21, 0.9
CONC = ["NET_INCOME", "EPS_BASIC", "TOTAL_EQUITY", "TOTAL_ASSETS", "REVENUE", "GROSS_PROFIT", "CFO", "TOTAL_LIABILITIES"]
VALSIG = ["EP", "BM", "SP", "CFP", "ROE", "ROA", "GPA", "MARGIN", "ACCRUAL", "LEV", "NIGROWTH", "REVGROWTH", "SUE"]

px = pd.read_parquet(f"var/_analysis/{PATH}.parquet")
px["date"] = pd.to_datetime(px["date"]); px["ticker"] = ZF(px["ticker"])
px = px.sort_values(["ticker", "date"])
g = px.groupby("ticker", group_keys=False)
px["dv"] = (px["close"] * px["volume"]).astype(float)
px["fwd"] = g["close"].shift(-21) / px["close"] - 1
px["advM"] = g["dv"].transform(lambda s: s.rolling(21, min_periods=10).median())
codes = px["ticker"].unique().tolist()

with session_scope() as s:
    A = pd.DataFrame(s.execute(text(
        "SELECT ticker, concept, value, as_of_ts, period_end FROM financial_facts "
        "WHERE market=:m AND period_kind='A' AND concept = ANY(:c) AND ticker = ANY(:t)"),
        {"m": MKT, "c": CONC, "t": codes}).all(), columns=["ticker", "concept", "value", "as_of", "pe"])
    Q = pd.DataFrame(s.execute(text(
        "SELECT ticker, value, as_of_ts, period_end FROM financial_facts "
        "WHERE market=:m AND period_kind='Q' AND concept='NET_INCOME' AND ticker = ANY(:t)"),
        {"m": MKT, "t": codes}).all(), columns=["ticker", "ni", "as_of", "pe"])
for D in (A, Q):
    D["ticker"] = ZF(D["ticker"])
    D["as_of"] = pd.to_datetime(D["as_of"]).dt.tz_localize(None)
A["value"] = pd.to_numeric(A["value"], errors="coerce")
Q["ni"] = pd.to_numeric(Q["ni"], errors="coerce")
w = A.sort_values(["ticker", "as_of", "pe"]).drop_duplicates(["ticker", "concept", "as_of"], keep="last") \
     .pivot_table(index=["ticker", "as_of"], columns="concept", values="value", aggfunc="last").reset_index()
for c in CONC:
    w[c] = w.get(c, np.nan)
sh = w["NET_INCOME"] / w["EPS_BASIC"].replace(0, np.nan)            # shares outstanding (derived)
w["EP"] = w["EPS_BASIC"]; w["_bps"] = w["TOTAL_EQUITY"] / sh; w["_sps"] = w["REVENUE"] / sh; w["_cps"] = w["CFO"] / sh
w["ROE"] = w["NET_INCOME"] / w["TOTAL_EQUITY"].replace(0, np.nan)
w["ROA"] = w["NET_INCOME"] / w["TOTAL_ASSETS"].replace(0, np.nan)
w["GPA"] = w["GROSS_PROFIT"] / w["TOTAL_ASSETS"].replace(0, np.nan)
w["MARGIN"] = w["NET_INCOME"] / w["REVENUE"].replace(0, np.nan)
w["ACCRUAL"] = -(w["NET_INCOME"] - w["CFO"]) / w["TOTAL_ASSETS"].replace(0, np.nan)
w["LEV"] = -(w["TOTAL_LIABILITIES"] / w["TOTAL_ASSETS"].replace(0, np.nan))
w = w.sort_values(["ticker", "as_of"])
gw = w.groupby("ticker", group_keys=False)
w["NIGROWTH"] = gw["NET_INCOME"].transform(lambda s: (s - s.shift(1)) / s.shift(1).abs())
w["REVGROWTH"] = gw["REVENUE"].transform(lambda s: (s - s.shift(1)) / s.shift(1).abs())
# quarterly SUE (seasonal diff standardized)
Q = Q.sort_values(["ticker", "pe"]).drop_duplicates(["ticker", "pe"], keep="last")
gq = Q.groupby("ticker", group_keys=False)
Q["_sd"] = gq["ni"].transform(lambda s: s - s.shift(4))
Q["SUE"] = gq.apply(lambda x: x["_sd"] / (x["_sd"].rolling(6, min_periods=4).std().abs() + 1e-9)).reset_index(level=0, drop=True) if len(Q) else np.nan
qsue = Q[["ticker", "as_of", "SUE"]].dropna()

print(f"### {UNI} FUNDAMENTALS  [cost={COST}bps, annual facts {w['ticker'].nunique()} tickers, SUE {qsue['ticker'].nunique()} tickers]", flush=True)


def pit_merge(sig_frame, cols):
    out = px.copy()
    for c in cols:
        f = sig_frame[["ticker", "as_of", c]].dropna(subset=[c]).sort_values("as_of")
        m = pd.merge_asof(out[["ticker", "date"]].sort_values("date"), f.rename(columns={"as_of": "date"}).sort_values("date"),
                          on="date", by="ticker", direction="backward")
        out = out.merge(m[["ticker", "date", c]], on=["ticker", "date"], how="left")
    return out


base = px[["ticker", "date", "fwd", "advM"]].copy()
merged = pit_merge(w, ["EP", "_bps", "_sps", "_cps", "ROE", "ROA", "GPA", "MARGIN", "ACCRUAL", "LEV", "NIGROWTH", "REVGROWTH"])
merged = merged.merge(pit_merge(qsue, ["SUE"])[["ticker", "date", "SUE"]], on=["ticker", "date"], how="left")
# value ratios use price: EP=eps/close, BM=bps/close, SP=sps/close, CFP=cps/close
merged["EP"] = merged["EP"] / merged["close"]; merged["BM"] = merged["_bps"] / merged["close"]
merged["SP"] = merged["_sps"] / merged["close"]; merged["CFP"] = merged["_cps"] / merged["close"]


def metrics(rows):
    R = pd.DataFrame(rows, columns=["exc", "turn", "bench", "cap", "ic"])
    e = R["exc"].values; turn = R["turn"].mean(skipna=True)
    net = e.mean() - (turn if turn == turn else 0) * 2 * COST / 1e4
    b2 = np.mean(np.sort(e)[:-2]) if len(e) > 2 else e.mean()
    pos = e[e > 0].sum(); conc5 = np.sort(e)[::-1][:5].clip(min=0).sum() / pos if pos > 0 else np.nan
    bear = np.nanmean(e[R["bench"].values <= np.quantile(R["bench"].values, 1/3)]); mid = len(e) // 2
    return dict(gross=e.mean(), net=net, ic=R["ic"].mean(skipna=True), b2=b2, conc5=conc5,
                bear=bear, h1=e[:mid].mean(), h2=e[mid:].mean(), turn=turn, cap=R["cap"].median(), n=len(e))


dates = np.sort(merged["date"].unique()); samp = dates[252::STEP]
print(f"{'signal':10s} {'grossExc':>9s} {'netExc':>8s} {'rankIC':>8s} {'best-2':>8s} {'conc5':>6s} {'bear':>7s} {'H1':>7s} {'H2':>7s} {'turn':>5s} {'cap($M)':>8s} {'cover':>6s}")
res = {}
for sig in VALSIG:
    rows = []; prev = None; cov = []
    for t in samp:
        d = merged[merged["date"] == t].dropna(subset=["fwd", sig, "advM"])
        cov.append(len(d))
        if len(d) < 20:
            continue
        sel = d[d[sig] >= d[sig].quantile(DEC)]
        cur = set(sel["ticker"]); turn = 1 - len(cur & prev) / len(cur | prev) if prev else np.nan; prev = cur
        rows.append((float(sel["fwd"].mean() - d["fwd"].mean()), turn, float(d["fwd"].mean()),
                     float(sel["advM"].median()), spearmanr(d[sig], d["fwd"]).correlation))
    if len(rows) > 10:
        res[sig] = metrics(rows)
        m = res[sig]
        robust = "  <=" if (m["net"] > 0 and m["b2"] > 0 and m["bear"] > 0 and m["h1"] > 0 and m["h2"] > 0 and (m["conc5"] != m["conc5"] or m["conc5"] < 0.7)) else ""
        print(f"{sig:10s} {m['gross']*100:+8.2f}% {m['net']*100:+7.2f}% {m['ic']:+8.4f} {m['b2']*100:+7.2f}% "
              f"{(m['conc5'] if m['conc5']==m['conc5'] else 0):5.0%} {m['bear']*100:+6.2f}% {m['h1']*100:+6.2f}% "
              f"{m['h2']*100:+6.2f}% {m['turn']:4.0%} {m['cap']/1e6:7.1f} {int(np.median(cov)):5d}{robust}", flush=True)
print("  <= = passes FULL gauntlet. cover=median #names/date with this fundamental.", flush=True)
