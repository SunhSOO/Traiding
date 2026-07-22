"""TEST 3-lite — is ILLIQ even RELEVANT to the production universe?
My ILLIQ win was on KR_MID (rank 200-500). Production trades KOSPI200+KOSDAQ150 (more
liquid). If that universe is too liquid for an illiquidity premium, ILLIQ is irrelevant
to production regardless of orthogonality. Loads ONLY 6 columns from the production cache
(tiny footprint — safe beside the running batch). Tests amihud top-decile net-of-cost in
the actual production universe + size(dollar-vol)-neutral version. cost 30bps/side.

If amihud tilt IS tradeable here → ILLIQ relevant, full-model orthogonality worth running.
If NOT → ILLIQ is a KR_MID-only signal, not applicable to production.
Usage: uv run python var/_analysis/orthogonality_lite.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd

COLS = ["date", "ticker", "ret_fwd_21d", "amihud_illiq_63d", "dollar_volume", "dollar_volume_z63"]
STEP, DEC, BPS = 21, 0.9, 30
d0 = pd.read_parquet("var/_bt_period_KR_2018-01-01_2024-01-01.parquet", columns=COLS)
d1 = pd.read_parquet("var/_bt_period_KR_2023-06-01_2026-07-01.parquet", columns=COLS)
df = pd.concat([d0, d1], ignore_index=True)
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
df = df.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last")
print(f"production universe: {len(df)} rows, {df['ticker'].nunique()} tickers, "
      f"{df['date'].min().date()}..{df['date'].max().date()}", flush=True)


def walk(sizeneutral=False):
    dates = np.sort(df["date"].unique()); samp = dates[::STEP]
    rows = []; prev = None
    for i, t in enumerate(samp):
        d = df[df["date"] == t].dropna(subset=["ret_fwd_21d", "amihud_illiq_63d"]).copy()
        if len(d) < 25:
            continue
        if sizeneutral:
            d = d.dropna(subset=["dollar_volume"])
            x = np.log(d["dollar_volume"].clip(lower=1)); y = d["amihud_illiq_63d"]
            b = np.polyfit(x, y, 1); d["S"] = y - (b[0] * x + b[1])
        else:
            d["S"] = d["amihud_illiq_63d"]
        if len(d) < 25:
            continue
        sel = d[d["S"] >= d["S"].quantile(DEC)]
        exc = float(sel["ret_fwd_21d"].mean() - d["ret_fwd_21d"].mean())
        cur = set(sel["ticker"]); turn = 1 - len(cur & prev) / len(cur | prev) if prev else np.nan; prev = cur
        rows.append((i, exc, turn, float(d["ret_fwd_21d"].mean())))
    R = np.array(rows); mid = len(samp) // 2
    turn = np.nanmean(R[:, 2]); net = R[:, 1].mean() - (turn if turn == turn else 0) * 2 * BPS / 1e4
    b2 = np.mean(np.sort(R[:, 1])[:-2]); bear = np.nanmean(R[:, 1][R[:, 3] <= np.quantile(R[:, 3], 1/3)])
    hn = lambda s: (s[:, 1].mean() - np.nanmean(s[:, 2]) * 2 * BPS / 1e4) if len(s) else np.nan
    return dict(gross=R[:, 1].mean(), turn=turn, net=net, b2=b2, bear=bear,
                h1=hn(R[R[:, 0] < mid]), h2=hn(R[R[:, 0] >= mid]))


print(f"\n===== ILLIQ tilt in PRODUCTION universe (amihud_illiq_63d top-decile, {BPS}bps) =====")
print(f"{'variant':16s} {'gross':>7s} {'turn':>5s} {'net':>7s} {'best-2':>7s} {'bear':>7s} {'H1':>7s} {'H2':>7s}  verdict")
for sn, lab in [(False, "amihud raw"), (True, "amihud size-neut")]:
    r = walk(sizeneutral=sn)
    ok = r["net"] > 0 and r["b2"] > 0 and r["bear"] > 0 and r["h1"] > 0 and r["h2"] > 0
    print(f"{lab:16s} {r['gross']*100:+6.2f}% {r['turn']:4.0%} {r['net']*100:+6.2f}% {r['b2']*100:+6.2f}% "
          f"{r['bear']*100:+6.2f}% {r['h1']*100:+6.2f}% {r['h2']*100:+6.2f}%  {'<== RELEVANT' if ok else 'not tradeable here'}", flush=True)
print("  if amihud raw is tradeable here → run full-model orthogonality. if not → ILLIQ is KR_MID-specific, not production-applicable.")
