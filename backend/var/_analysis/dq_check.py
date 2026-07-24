"""Data-quality checks flagged in my own taxonomy but not yet run (execute while the
completeness audit runs): KR pykrx split-adjustment (I used adjusted=True but never
verified) + stale-price rate (zero-return / repeated-close) per universe — stale prices
inflate illiquidity signals and depress measured returns. All 9 universes.
Usage: uv run python var/_analysis/dq_check.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd

UNIS = [("US_LARGE", "px_US_LARGE"), ("US_MID", "px_US_MID"), ("US_SMALL", "px_US_SMALL"),
        ("US_BROAD", "px_US_BROAD"), ("KR_LARGE", "px_KR_LARGE_PYKRX"), ("KR_MID", "px_KR_MID_PYKRX"),
        ("KR_SMALL", "px_KR_SMALL"), ("KR_MICRO", "px_KR_MICRO_PYKRX"), ("TW_SMALL", "px_TW_SMALL")]

print(f"{'universe':10s} {'tickers':>7s} {'split>80%':>9s} {'worstmove':>9s} {'zero-ret%':>9s} {'repeat-close%':>13s} {'flag'}")
for name, path in UNIS:
    try:
        d = pd.read_parquet(f"var/_analysis/{path}.parquet")
    except Exception:
        print(f"{name:10s} MISSING"); continue
    d["date"] = pd.to_datetime(d["date"]); d = d.sort_values(["ticker", "date"])
    d["r"] = d.groupby("ticker")["close"].pct_change()
    nt = d["ticker"].nunique()
    split = d[d["r"].abs() > 0.8]["ticker"].nunique()
    worst = d["r"].abs().max()
    zero = (d["r"].abs() < 1e-9).mean()                          # zero-return days (stale)
    rc = d.groupby("ticker")["close"].apply(lambda s: (s.diff() == 0).mean()).mean()  # repeated-close
    fl = []
    if split > 0.02 * nt:
        fl.append("SPLIT오염")
    if zero > 0.06:
        fl.append("stale↑")
    print(f"{name:10s} {nt:7d} {split:9d} {worst:8.1f}x {zero*100:8.1f}% {rc*100:12.1f}% {' '.join(fl)}", flush=True)
print("\n  split>80%: 미조정 스플릿 신호(>2%종목이면 오염). zero-ret/repeat-close: stale price(>6%면 유동성/데이터 우려).")
print("  KR pykrx는 adjusted=True로 받았음 — split>80%이 0에 가까우면 조정 확인됨.")
