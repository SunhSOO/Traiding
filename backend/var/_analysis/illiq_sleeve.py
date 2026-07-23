"""C — KR_LARGE ILLIQ63 deployable strategy sleeve. Realistic long-only tilt within
KOSPI200: monthly select top-decile illiquidity, equal-weight, hold 21d, net 30bps.
Reports the actual portfolio equity curve (not just decile-mean-excess): absolute
return/Sharpe/MDD (with market beta) AND the market-neutral alpha (excess vs universe),
plus a capacity estimate (deployable AUM from the selected names' $ADV at a participation
cap). Non-overlapping 21d periods → honest independent returns.
Usage: uv run python var/_analysis/illiq_sleeve.py [KR_LARGE|KR_MID|KR_MICRO]
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd

UNI = sys.argv[1] if len(sys.argv) > 1 else "KR_LARGE"
PATH = {"KR_LARGE": "px_KR_LARGE_PYKRX", "KR_MID": "px_KR_MID_PYKRX", "KR_MICRO": "px_KR_MICRO_PYKRX",
        "KR_SMALL": "px_KR_SMALL", "US_LARGE": "px_US_LARGE", "US_MID": "px_US_MID",
        "US_SMALL": "px_US_SMALL", "US_BROAD": "px_US_BROAD", "TW_SMALL": "px_TW_SMALL"}[UNI]
COST = {"KR_LARGE": 30, "KR_MID": 40, "KR_MICRO": 120, "KR_SMALL": 60,
        "US_LARGE": 15, "US_MID": 20, "US_SMALL": 25, "US_BROAD": 25, "TW_SMALL": 60}[UNI]
STEP, DEC, PART = 21, 0.9, 0.05           # participation cap 5% of ADV/day, build over ~4 days
PPY = 252 / STEP

df = pd.read_parquet(f"var/_analysis/{PATH}.parquet")
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
df = df.sort_values(["ticker", "date"]); g = df.groupby("ticker", group_keys=False)
df["dv"] = (df["close"] * df["volume"]).astype(float)
df["_ai"] = (g["close"].pct_change().abs()) / df["dv"].replace(0, np.nan)
df["ILLIQ63"] = g["_ai"].transform(lambda s: s.rolling(63, min_periods=32).mean())
df["fwd"] = g["close"].shift(-21) / df["close"] - 1
df["advM"] = g["dv"].transform(lambda s: s.rolling(21, min_periods=10).median())

dates = np.sort(df["date"].unique()); samp = dates[252::STEP]
port, bench, turns, advs, nsel = [], [], [], [], []; prev = None
for t in samp:
    d = df[df["date"] == t].dropna(subset=["fwd", "ILLIQ63", "advM"])
    if len(d) < 25:
        continue
    sel = d[d["ILLIQ63"] >= d["ILLIQ63"].quantile(DEC)]
    port.append(float(sel["fwd"].mean())); bench.append(float(d["fwd"].mean()))
    cur = set(sel["ticker"]); turns.append(1 - len(cur & prev) / len(cur | prev) if prev else 1.0); prev = cur
    advs.append(float(sel["advM"].median())); nsel.append(len(sel))

port = np.array(port); bench = np.array(bench); turn = np.mean(turns)
cost_per = turn * 2 * COST / 1e4
netport = port - cost_per; excess = port - bench; netexcess = excess - cost_per


def stats(r):
    ann = r.mean() * PPY; vol = r.std() * np.sqrt(PPY); sharpe = ann / vol if vol > 0 else np.nan
    eq = np.cumprod(1 + r); mdd = float((eq / np.maximum.accumulate(eq) - 1).min())
    return ann, vol, sharpe, mdd, float(eq[-1] - 1), (r > 0).mean()


print(f"### {UNI} ILLIQ63 SLEEVE  [cost={COST}bps, {len(port)} non-overlapping 21d periods, avg {np.mean(nsel):.0f} names]")
for lab, r in [("absolute (with beta)", netport), ("benchmark (universe EW)", bench),
               ("ALPHA (excess, mkt-neutral)", netexcess)]:
    a, v, s, m, tot, hit = stats(r)
    print(f"  {lab:28s}: ann {a*100:+6.2f}%  vol {v*100:5.1f}%  Sharpe {s:+.2f}  MDD {m*100:6.1f}%  cum {tot*100:+7.1f}%  hit {hit:.0%}")
print(f"  turnover {turn:.0%}/reb  cost drag {cost_per*PPY*100:.1f}%/yr")
admed = np.median(advs)
print(f"\n  CAPACITY: selected names median $ADV = ${admed/1e6:.0f}M. at {PART:.0%} participation × 4-day build")
print(f"    → ~${admed*PART*4/1e6:.1f}M per name × {np.mean(nsel):.0f} names = deployable ≈ ${admed*PART*4*np.mean(nsel)/1e9:.2f}B AUM")
print("  (absolute = tradeable P&L incl. KOSPI beta; ALPHA = the illiquidity edge net of market & cost.)")
