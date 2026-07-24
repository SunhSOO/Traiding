"""PROACTIVE gap verification (user: "did you do ALL verifications? missing something?").
Check the highest-risk UN-verified things before being asked:
  1. LEAK SCAN — all 478 production features: per-date rank-IC vs fwd. |IC|>0.10 = leak
     suspect (legit x-sectional feats are <~0.06; vol_adj_ret_21d was 0.94). If more leaks
     exist, the 시점24-26 production re-audit is contaminated.
  2. US PRICE ADJUSTMENT — split/dividend: count single-day |return|>80% (unadjusted-split
     signature) per US universe. If many, US results are garbage.
  3. SURVIVORSHIP breadth — fraction of names entering after 2018 (late-entrant bias),
     for US_LARGE + KR_MID + KR_MICRO (only KR_LARGE was quantified).
Usage: uv run python var/_analysis/verify_gaps.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from scripts.train_lgbm import ALL_FEATURE_COLS

print("=" * 70)
print("1) LEAK SCAN — all 478 features, per-date rank-IC vs ret_fwd_21d")
print("=" * 70)
for mkt in ("KR", "US"):
    d = pd.read_parquet(f"var/_bt_period_{mkt}_2018-01-01_2024-01-01.parquet")
    d["date"] = pd.to_datetime(d["date"])
    feats = [c for c in ALL_FEATURE_COLS if c in d.columns]
    dates = np.sort(d["date"].unique())[::21]   # sample monthly for speed
    ics = {}
    for f in feats:
        v = []
        for t in dates:
            g = d[d["date"] == t].dropna(subset=["ret_fwd_21d", f])
            if len(g) > 30:
                c = spearmanr(g[f], g["ret_fwd_21d"]).correlation
                if c == c:
                    v.append(c)
        if v:
            ics[f] = np.mean(v)
    susp = {f: c for f, c in ics.items() if abs(c) > 0.10}
    print(f"\n[{mkt}] {len(feats)} feats scanned. LEAK SUSPECTS (|IC|>0.10): {len(susp)}")
    for f, c in sorted(susp.items(), key=lambda x: -abs(x[1])):
        print(f"    {c:+.3f}  {f}   <== LEAK")
    top = sorted(ics.items(), key=lambda x: -abs(x[1]))[:5]
    print(f"    (top-5 by |IC| overall: " + ", ".join(f"{f}={c:+.3f}" for f, c in top) + ")")

print("\n" + "=" * 70)
print("2) US PRICE ADJUSTMENT — single-day |return|>80% (unadjusted-split signature)")
print("=" * 70)
for uni, path in [("US_LARGE", "px_US_LARGE"), ("US_MID", "px_US_MID"), ("US_SMALL", "px_US_SMALL")]:
    d = pd.read_parquet(f"var/_analysis/{path}.parquet")
    d = d.sort_values(["ticker", "date"])
    d["r"] = d.groupby("ticker")["close"].pct_change()
    big = d[d["r"].abs() > 0.8]
    print(f"  {uni}: {big['ticker'].nunique()} tickers with a >80% single-day move "
          f"(of {d['ticker'].nunique()}); worst {d['r'].abs().max():.1f}x")

print("\n" + "=" * 70)
print("3) SURVIVORSHIP breadth — late-entrant fraction (only KR_LARGE was quantified before)")
print("=" * 70)
for uni, path in [("US_LARGE", "px_US_LARGE"), ("KR_MID", "px_KR_MID_PYKRX"), ("KR_MICRO", "px_KR_MICRO_PYKRX")]:
    d = pd.read_parquet(f"var/_analysis/{path}.parquet")
    d["date"] = pd.to_datetime(d["date"])
    first = d.groupby("ticker")["date"].min()
    n = len(first); late = (first > pd.Timestamp("2018-06-01")).sum()
    print(f"  {uni}: {late}/{n} ({late/n:.0%}) entered after 2018-06 (late-entrant survivorship, measurable part)")
print("\n  (dropped/delisted names absent from all current-member caches = unmeasurable half, all universes.)")
