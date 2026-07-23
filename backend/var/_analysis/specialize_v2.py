"""A (orthogonality) + B (new signals). Extends universe_specialize with:
 B new signals (close/high/low/volume only — no open, so no overnight/gap):
   CS = Corwin-Schultz high-low bid-ask spread proxy (microstructure illiquidity)
   COSKEW = -coskewness with market (lottery/crash cousin)
   VOLTREND = dollar-vol 21d / 252d (rising participation)
   HLRANGE = mean (high-low)/close over 21d (range-vol proxy)
   LO52 = -(close / 252d-min)  (proximity to 52wk LOW; distressed)
 A orthogonality: size-neutral variants ILLIQ63_SN / ILLIQ126_SN (residual of the
   signal on SIZE cross-sectionally each date) → if it still passes the gauntlet, the
   illiquidity premium is DISTINCT from a pure small-cap tilt; if it collapses, it's size.
Full gauntlet (net@cost, best-2, conc5, bear, split-half) + turnover + capacity.
Usage: uv run python var/_analysis/specialize_v2.py [UNI ...]
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy.stats import spearmanr

UNIS = [("KR_LARGE", "px_KR_LARGE_PYKRX", 30), ("KR_MID", "px_KR_MID_PYKRX", 40),
        ("KR_MICRO", "px_KR_MICRO_PYKRX", 120), ("US_LARGE", "px_US_LARGE", 15),
        ("US_SMALL", "px_US_SMALL", 25), ("KR_SMALL", "px_KR_SMALL", 60),
        ("US_MID", "px_US_MID", 20), ("US_BROAD", "px_US_BROAD", 25), ("TW_SMALL", "px_TW_SMALL", 60)]
NEW = ["CS", "COSKEW", "VOLTREND", "HLRANGE", "LO52"]
REF = ["ILLIQ63", "ILLIQ126", "SIZE"]                 # references to compare against
SN = ["ILLIQ63_SN", "ILLIQ126_SN"]                    # size-neutral variants
STEP, DEC = 21, 0.9


def build(df):
    df = df.sort_values(["date", "ticker"]).copy()
    df["ret1"] = df.groupby("ticker")["close"].pct_change()
    df["mkt"] = df.groupby("date")["ret1"].transform("mean")
    df["dv"] = (df["close"] * df["volume"]).astype(float)
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)   # clean positional index
    g = df.groupby("ticker", group_keys=False)
    tr = lambda col, w, fn="mean", mp=None: g[col].transform(lambda s: getattr(s.rolling(w, min_periods=mp or w // 2), fn)())
    df["_ai"] = df["ret1"].abs() / df["dv"].replace(0, np.nan)
    df["ILLIQ63"] = tr("_ai", 63, "mean", 32); df["ILLIQ126"] = tr("_ai", 126, "mean", 63)
    df["SIZE"] = -np.log(tr("dv", 63, "median", 20).clip(lower=1))
    # Corwin-Schultz 2-day high/low bid-ask spread proxy (all transform-safe)
    df["_hl"] = np.log(df["high"] / df["low"].replace(0, np.nan)) ** 2
    b = tr("_hl", 2, "sum", 2)
    df["_hi2"] = tr("high", 2, "max", 2); df["_lo2"] = tr("low", 2, "min", 2)
    gm = np.log(df["_hi2"] / df["_lo2"].replace(0, np.nan)) ** 2
    k = 3 - 2 * 2 ** 0.5
    alpha = (np.sqrt(2 * b) - np.sqrt(b)) / k - np.sqrt((gm / k).clip(lower=0))
    df["_cs"] = (2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))).clip(lower=0)
    df["CS"] = tr("_cs", 21, "mean", 10)
    # coskewness = corr(resid, mkt^2) via transform-safe cov/std pieces
    df["resid"] = df["ret1"] - df["mkt"]; df["_m2"] = df["mkt"] ** 2; df["_rm2"] = df["resid"] * df["_m2"]
    cov = tr("_rm2", 60, "mean", 30) - tr("resid", 60, "mean", 30) * tr("_m2", 60, "mean", 30)
    df["COSKEW"] = -(cov / (tr("resid", 60, "std", 30) * tr("_m2", 60, "std", 30) + 1e-12))
    df["VOLTREND"] = tr("dv", 21, "mean", 10) / (tr("dv", 252, "mean", 120) + 1e-9)
    df["_hlc"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["HLRANGE"] = tr("_hlc", 21, "mean", 10)
    df["LO52"] = -(df["close"] / tr("close", 252, "min", 120))
    df["fwd"] = g["close"].shift(-21) / df["close"] - 1
    df["advM"] = tr("dv", 21, "median", 10)
    return df


def metrics(rows, cost):
    R = pd.DataFrame(rows, columns=["exc", "turn", "bench", "cap", "ic"])
    e = R["exc"].values; turn = R["turn"].mean(skipna=True)
    net = e.mean() - (turn if turn == turn else 0) * 2 * cost / 1e4
    b2 = np.mean(np.sort(e)[:-2]) if len(e) > 2 else e.mean()
    pos = e[e > 0].sum(); conc5 = np.sort(e)[::-1][:5].clip(min=0).sum() / pos if pos > 0 else np.nan
    bear = np.nanmean(e[R["bench"].values <= np.quantile(R["bench"].values, 1/3)]); mid = len(e) // 2
    return dict(net=net, ic=R["ic"].mean(skipna=True), b2=b2, conc5=conc5, bear=bear,
                h1=e[:mid].mean(), h2=e[mid:].mean(), turn=turn, cap=R["cap"].median())


def one(df, sig, cost, sizeneutral=False):
    dates = np.sort(df["date"].unique()); samp = dates[252::STEP]; rows = []; prev = None
    src = sig.replace("_SN", "")
    for t in samp:
        d = df[df["date"] == t].dropna(subset=["fwd", src, "advM", "SIZE"]).copy()
        if len(d) < 25:
            continue
        s = d[src]
        if sizeneutral:
            x = d["SIZE"].values; y = s.values; b = np.polyfit(x, y, 1); s = pd.Series(y - (b[0] * x + b[1]), index=d.index)
        sel = d[s >= s.quantile(DEC)]
        cur = set(sel["ticker"]); turn = 1 - len(cur & prev) / len(cur | prev) if prev else np.nan; prev = cur
        rows.append((float(sel["fwd"].mean() - d["fwd"].mean()), turn, float(d["fwd"].mean()),
                     float(sel["advM"].median()), spearmanr(s, d["fwd"]).correlation))
    return metrics(rows, cost) if len(rows) > 10 else None


targets = sys.argv[1:] or [u[0] for u in UNIS]
for name, path, cost in UNIS:
    if name not in targets:
        continue
    df = pd.read_parquet(f"var/_analysis/{path}.parquet")
    df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
    df = build(df)
    print(f"\n### {name} [cost={cost}bps]  (B=new signals, A=size-neutral ILLIQ vs raw/SIZE)", flush=True)
    print(f"{'signal':13s} {'net':>7s} {'rankIC':>8s} {'best-2':>8s} {'conc5':>6s} {'bear':>7s} {'H1':>7s} {'H2':>7s} {'turn':>5s} {'cap($M)':>8s}")
    order = REF + SN + NEW
    rr = {}
    for sig in order:
        r = one(df, sig, cost, sizeneutral=sig.endswith("_SN"))
        if r is None:
            continue
        rr[sig] = r
        robust = "  <=" if (r["net"] > 0 and r["b2"] > 0 and r["bear"] > 0 and r["h1"] > 0 and r["h2"] > 0 and (r["conc5"] != r["conc5"] or r["conc5"] < 0.7)) else ""
        print(f"{sig:13s} {r['net']*100:+6.2f}% {r['ic']:+8.4f} {r['b2']*100:+7.2f}% "
              f"{(r['conc5'] if r['conc5']==r['conc5'] else 0):5.0%} {r['bear']*100:+6.2f}% {r['h1']*100:+6.2f}% "
              f"{r['h2']*100:+6.2f}% {r['turn']:4.0%} {r['cap']/1e6:7.1f}{robust}", flush=True)
print("\n  A: ILLIQ63_SN (size-neutral) vs ILLIQ63 raw & SIZE — SN still robust => distinct illiquidity premium. B: any NEW beating ILLIQ?", flush=True)
