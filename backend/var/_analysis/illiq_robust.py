"""TEST 2b — harden the ILLIQ winner before the heavy orthogonality test.
(1) cost sweep 40..100bps (does the low-turnover edge survive higher cost?)
(2) split-half H1 2018-22 / H2 2022-26 (regime-robust, not one-half luck?)
(3) size-neutralized ILLIQ = residual of ILLIQ on log(median $vol) — is it a genuine
    illiquidity premium or just a small-cap/size proxy?

KR_MID (primary tradeable target) + KR_MICRO. Top-decile long-vs-universe excess.
Usage: uv run python var/_analysis/illiq_robust.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd

UNIS = [("KR_MID", "px_KR_MID_PYKRX"), ("KR_MICRO", "px_KR_MICRO_PYKRX")]
STEP, DEC = 21, 0.9


def prep(df):
    df = df.sort_values(["ticker", "date"]).copy()
    g = df.groupby("ticker", group_keys=False)
    df["ret1"] = g["close"].pct_change()
    dv = (df["close"] * df["volume"]).replace(0, np.nan)
    df["ai1"] = df["ret1"].abs() / dv
    df["ILLIQ"] = g["ai1"].transform(lambda s: s.rolling(21, min_periods=10).mean())
    df["dvol"] = g.apply(lambda x: (x["close"] * x["volume"]).rolling(63, min_periods=20).median()).reset_index(level=0, drop=True)
    df["fwd"] = g["close"].shift(-21) / df["close"] - 1
    return df


def walk(df, sizeneutral=False):
    dates = np.sort(df["date"].unique()); samp = dates[252::STEP]
    rows = []; prev = None
    for i, t in enumerate(samp):
        d = df[df["date"] == t].dropna(subset=["fwd", "ILLIQ"]).copy()
        if sizeneutral:
            d = d.dropna(subset=["dvol"])
            if len(d) < 25:
                continue
            x = np.log(d["dvol"].clip(lower=1)); y = d["ILLIQ"]
            b = np.polyfit(x, y, 1); d["S"] = y - (b[0] * x + b[1])   # ILLIQ resid of size
        else:
            d["S"] = d["ILLIQ"]
        if len(d) < 25:
            continue
        sel = d[d["S"] >= d["S"].quantile(DEC)]
        exc = float(sel["fwd"].mean() - d["fwd"].mean())
        cur = set(sel["ticker"]); turn = 1 - len(cur & prev) / len(cur | prev) if prev else np.nan; prev = cur
        rows.append((i, exc, turn, float(d["fwd"].mean())))
    R = np.array(rows); mid = len(samp) // 2

    def stat(sub, bps):
        e = sub[:, 1]; turn = np.nanmean(sub[:, 2])
        return e.mean() - (turn if turn == turn else 0) * 2 * bps / 1e4
    full = R
    turn = np.nanmean(R[:, 2]); b2 = np.mean(np.sort(R[:, 1])[:-2])
    bear = np.nanmean(R[:, 1][R[:, 3] <= np.quantile(R[:, 3], 1/3)])
    h1 = R[R[:, 0] < mid]; h2 = R[R[:, 0] >= mid]
    return dict(gross=R[:, 1].mean(), turn=turn, b2=b2, bear=bear,
                n40=stat(full, 40), n60=stat(full, 60), n80=stat(full, 80), n100=stat(full, 100),
                h1=stat(h1, 60), h2=stat(h2, 60))


print(f"{'universe':10s} {'variant':13s} {'gross':>7s} {'turn':>5s} {'net@40':>7s} {'net@60':>7s} {'net@80':>7s} {'net@100':>8s} {'best-2':>7s} {'bear':>7s} {'H1@60':>7s} {'H2@60':>7s}")
for name, path in UNIS:
    df = pd.read_parquet(f"var/_analysis/{path}.parquet")
    df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
    df = prep(df)
    for sn, lab in [(False, "ILLIQ raw"), (True, "ILLIQ size-neut")]:
        r = walk(df, sizeneutral=sn)
        print(f"{name:10s} {lab:13s} {r['gross']*100:+6.2f}% {r['turn']:4.0%} {r['n40']*100:+6.2f}% {r['n60']*100:+6.2f}% "
              f"{r['n80']*100:+6.2f}% {r['n100']*100:+7.2f}% {r['b2']*100:+6.2f}% {r['bear']*100:+6.2f}% "
              f"{r['h1']*100:+6.2f}% {r['h2']*100:+6.2f}%", flush=True)
    print()
print("  tradeable-robust iff net stays + across cost sweep AND both H1/H2 + AND size-neutral keeps it (not pure size).")
