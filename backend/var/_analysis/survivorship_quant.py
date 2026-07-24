"""QUANTIFY the survivorship deflator on KR_LARGE ILLIQ63 — instead of hand-waving
"can't trust the size", MEASURE how much survivorship actually removes. Bias sources:
(a) late entrants (grew INTO top-200 → pre-entry returns upward-biased),
(b) dropped/delisted names ABSENT from the current-members cache.
Test: re-run the ILLIQ excess on the "always-present since 2018" subset (removes (a));
if the excess survives, late-entrant survivorship does NOT explain the magnitude. (b) is
bounded separately (can't price absent names). Point: DON'T reject a high number without
quantifying whether the claimed flaw accounts for it.
Usage: uv run python var/_analysis/survivorship_quant.py
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
STEP, DEC, COST = 21, 0.9, 30; PPY = 252 / STEP

first = df.groupby("ticker")["date"].min()
always = set(first[first <= pd.Timestamp("2018-03-01")].index)   # present from the start
print(f"KR_LARGE: {df['ticker'].nunique()} names; always-present-since-2018: {len(always)} "
      f"({len(always)/df['ticker'].nunique():.0%}); late entrants dropped: {df['ticker'].nunique()-len(always)}")


def run(universe):
    d0 = df[df["ticker"].isin(universe)] if universe else df
    dates = np.sort(d0["date"].unique()); samp = dates[252::STEP]
    exc, turns = [], []; prev = None
    for t in samp:
        d = d0[d0["date"] == t].dropna(subset=["fwd", "ILLIQ63"])
        if len(d) < 20:
            continue
        sel = d[d["ILLIQ63"] >= d["ILLIQ63"].quantile(DEC)]
        exc.append(float(sel["fwd"].mean() - d["fwd"].mean()))
        cur = set(sel["ticker"]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else 1.0); prev = cur
    e = np.array(exc); turn = np.nanmean(turns)
    net = e.mean() - turn * 2 * COST / 1e4
    return net * PPY, net * PPY / (e.std() * np.sqrt(PPY)), len(e)


a_all, s_all, n_all = run(None)
a_alw, s_alw, n_alw = run(always)
print(f"\n{'universe':28s} {'net ann excess':>14s} {'ann Sharpe':>11s}")
print(f"{'ALL (current members)':28s} {a_all*100:+13.1f}% {s_all:+10.2f}")
print(f"{'ALWAYS-present-since-2018':28s} {a_alw*100:+13.1f}% {s_alw:+10.2f}")
drop = (a_all - a_alw) / a_all * 100 if a_all else 0
print(f"\n  late-entrant survivorship removes: {(a_all-a_alw)*100:+.1f}%p ({drop:.0f}% of the excess)")
print(f"  → if the always-present excess is still large, survivorship(late-entry) does NOT explain the magnitude.")
print(f"  ⚠️ NOT captured here: dropped/delisted names (absent from cache) — the harder, unmeasurable half.")
