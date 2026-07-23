"""N3 — Post-Earnings-Announcement-Drift (information-diffusion thesis).
Less-efficient markets drift more after earnings surprises. Event study on KR
quarterly NET_INCOME (financial_facts, period_kind='Q', PIT via as_of_ts = the
disclosure date). SUE = seasonal-diff earnings standardized by its own rolling
std (standardized unexpected earnings, seasonal-random-walk model). For each
disclosure, forward H-day return from the first trading day on/after disclosure.
PEAD metric = rank-IC(SUE, fwd) + top-minus-bottom-quintile spread, per universe.

HONEST LIMIT: financial_facts covers liquid KR only (large/mid) — the thesis says
drift is WEAKEST there. Small-cap (where it should be strong) is DART-ingest-gated.
Usage: uv run python var/_analysis/pead.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from sqlalchemy import text
from core.db import session_scope

HS = [21, 42, 63]
BPS = 30

# ── prices (clean pykrx), tagged by universe ────────────────────────────────
pxs = {}
for uni, path in [("KR_LARGE", "px_KR_LARGE_PYKRX"), ("KR_MID", "px_KR_MID_PYKRX")]:
    d = pd.read_parquet(f"var/_analysis/{path}.parquet")
    d["date"] = pd.to_datetime(d["date"]); d["ticker"] = d["ticker"].astype(str).str.zfill(6)
    for tk, g in d.sort_values("date").groupby("ticker"):
        pxs[tk] = (uni, g["date"].values, g["close"].values.astype(float))
print(f"prices: {len(pxs)} tickers", flush=True)

# ── quarterly earnings with disclosure date ─────────────────────────────────
with session_scope() as s:
    rows = s.execute(text(
        "SELECT ticker, value, as_of_ts, period_end FROM financial_facts "
        "WHERE market='KR' AND period_kind='Q' AND concept='NET_INCOME' AND as_of_ts IS NOT NULL")).all()
ff = pd.DataFrame(rows, columns=["ticker", "ni", "as_of", "period_end"])
ff["ticker"] = ff["ticker"].astype(str).str.zfill(6)
ff["ni"] = pd.to_numeric(ff["ni"], errors="coerce")
ff["as_of"] = pd.to_datetime(ff["as_of"]).dt.tz_localize(None)
ff["period_end"] = pd.to_datetime(ff["period_end"])
ff = ff.dropna(subset=["ni"]).sort_values(["ticker", "period_end"]).drop_duplicates(["ticker", "period_end"], keep="last")

# ── SUE = seasonal diff / rolling std of seasonal diff (per ticker) ──────────
def sue(g):
    g = g.sort_values("period_end").copy()
    g["sdiff"] = g["ni"] - g["ni"].shift(4)                    # vs same quarter last year
    g["sue"] = g["sdiff"] / (g["sdiff"].rolling(6, min_periods=4).std().abs() + 1e-9)
    return g
ff = ff.groupby("ticker", group_keys=False).apply(sue).dropna(subset=["sue"])
print(f"earnings events with SUE: {len(ff)} ({ff['ticker'].nunique()} tickers), {ff['as_of'].min().date()}..{ff['as_of'].max().date()}", flush=True)


def fwd_ret(tk, as_of, H):
    if tk not in pxs:
        return None, None
    _, dts, cl = pxs[tk]
    i = np.searchsorted(dts, np.datetime64(as_of))            # first trading day on/after disclosure
    if i >= len(dts) - H:
        return None, None
    return pxs[tk][0], cl[i + H] / cl[i] - 1                  # (universe, fwd H-day return)


print(f"\n===== PEAD event study (KR quarterly, {BPS}bps single round-trip) =====")
print(f"{'universe':9s} {'H':>4s} {'events':>7s} {'rankIC':>8s} {'Q5-Q1':>8s} {'net(Q5-Q1)':>11s} {'topDgross':>10s}")
for uni in ["KR_LARGE", "KR_MID", "ALL"]:
    for H in HS:
        S, F = [], []
        for _, r in ff.iterrows():
            u, fr = fwd_ret(r["ticker"], r["as_of"], H)
            if fr is None:
                continue
            if uni != "ALL" and u != uni:
                continue
            S.append(r["sue"]); F.append(fr)
        if len(S) < 50:
            print(f"{uni:9s} {H:4d} {len(S):7d}   (too few)"); continue
        S, F = np.array(S), np.array(F)
        ic = spearmanr(S, F).correlation
        q = pd.qcut(pd.Series(S).rank(method="first"), 5, labels=False)
        q5 = F[q == 4].mean(); q1 = F[q == 0].mean(); spread = q5 - q1
        topd = F[S >= np.quantile(S, 0.9)].mean() - F.mean()
        net = spread - 2 * BPS / 1e4                          # one entry+exit round trip over H days
        print(f"{uni:9s} {H:4d} {len(S):7d} {ic:+8.4f} {spread*100:+7.2f}% {net*100:+10.2f}% {topd*100:+9.2f}%", flush=True)
print("  PEAD real iff rankIC>0 & Q5-Q1 spread>0 net-of-cost; thesis: stronger in less-efficient (KR_MID>KR_LARGE).")
