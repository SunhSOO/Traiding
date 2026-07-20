"""Exhaustive defensive-overlay sweep: test EVERY tractable exposure signal on
the KR + US equal-weight market (2016-2026), 1-day-lagged (no look-ahead).
Rank by Calmar (CAGR/|MaxDD|) and Sharpe. The overlay scales whatever you hold,
so this ranks the DEFENSE value of each signal cleanly."""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd
from sqlalchemy import text
from core.db import session_scope

def load(market):
    with session_scope() as s:
        rows = s.execute(text("SELECT trade_date,ticker,close FROM daily_prices "
            "WHERE market=:m AND trade_date>='2016-01-01'"), {"m": market}).all()
    px = pd.DataFrame(rows, columns=["d","t","c"]); px["d"]=pd.to_datetime(px["d"]); px["c"]=px["c"].astype(float)
    cp = px.pivot_table(index="d", columns="t", values="c", aggfunc="last").sort_index()
    return cp

def stats(r):
    r = r.dropna();
    if len(r) < 60: return (np.nan,)*5
    cum = (1+r).cumprod(); dd = (cum/cum.cummax()-1).min()
    yrs = len(r)/252; cagr = cum.iloc[-1]**(1/yrs)-1
    sh = r.mean()/r.std()*np.sqrt(252) if r.std()>0 else 0
    calmar = cagr/abs(dd) if dd < 0 else np.nan
    return cum.iloc[-1]-1, cagr, dd, sh, calmar

def signals(cp):
    ew = cp.pct_change().mean(axis=1)                      # equal-weight market return
    idx = (1+ew).cumprod()                                  # index level
    out = {"ewret": ew}
    for n in (20, 50, 100, 200):
        out[f"b{n}"] = (cp > cp.rolling(n).mean()).mean(axis=1)      # breadth
        out[f"trend{n}"] = (idx > idx.rolling(n).mean()).astype(float)  # index vs own SMA
    out["mom60"] = (idx/idx.shift(60)-1)
    rv = ew.rolling(20).std()*np.sqrt(252)                  # realized vol (annualized)
    out["rv"] = rv
    out["dd"] = idx/idx.cummax()-1                          # running drawdown
    # cross-sectional trend proxy (implementable in selection.py from px_vs_sma50)
    pxvs50 = cp/cp.rolling(50).mean() - 1
    out["xs_mean50"] = pxvs50.mean(axis=1)                  # avg stock distance above its 50d SMA
    out["xs_med50"] = pxvs50.median(axis=1)
    return out

def run(market):
    cp = load(market); sg = signals(cp); ew = sg["ewret"]
    tgt = sg["rv"].median()
    configs = {
        "buy&hold(무방어)":        pd.Series(1.0, index=ew.index),
        "breadth20 (0.4+b)":      (0.4+sg["b20"]).clip(upper=1),
        "breadth50 (현재채택)":    (0.4+sg["b50"]).clip(upper=1),
        "breadth100":             (0.4+sg["b100"]).clip(upper=1),
        "breadth200 (구)":        (0.4+sg["b200"]).clip(upper=1),
        "breadth50_steep":        ((sg["b50"]-0.25)/0.5).clip(0,1),
        "trend idx>sma50":        sg["trend50"].clip(0.3,1),
        "trend idx>sma200":       sg["trend200"].clip(0.3,1),
        "momentum60>0":           (sg["mom60"]>0).astype(float).clip(0.3,1),
        "vol-target":             (tgt/sg["rv"]).clip(0.2,1),
        "vol-spike(>80pct컷)":    (sg["rv"]<sg["rv"].rolling(252).quantile(0.8)).astype(float).clip(0.4,1),
        "drawdown<-10%컷":        (sg["dd"]>-0.10).astype(float).clip(0.4,1),
        "combo b50×trend50":      ((0.4+sg["b50"]).clip(upper=1)*sg["trend50"]).clip(0.3,1),
        "combo voltgt×b50":       ((tgt/sg["rv"]).clip(0.2,1)*(0.4+sg["b50"]).clip(upper=1)),
        "XS: mean50>0 (근사추세)":  (sg["xs_mean50"]>0).astype(float).clip(0.3,1),
        "XS: med50>0":            (sg["xs_med50"]>0).astype(float).clip(0.3,1),
        "XS: mean50 scaled":      (0.5+sg["xs_mean50"]*5).clip(0.3,1),
        "combo trend×voltgt":     ((sg["xs_mean50"]>0).astype(float).clip(0.4,1)*(tgt/sg["rv"]).clip(0.3,1)),
        "combo trend×volspike":   ((sg["xs_mean50"]>0).astype(float).clip(0.4,1)*(sg["rv"]<sg["rv"].rolling(252).quantile(0.8)).astype(float).clip(0.5,1)),
        "combo trend|voltgt(min)":pd.concat([(sg["xs_mean50"]>0).astype(float).clip(0.4,1),(tgt/sg["rv"]).clip(0.3,1)],axis=1).min(axis=1),
    }
    recent = (ew.index >= "2026-05-25")
    print(f"\n===== {market}  (EW market 2016-2026, overlay 1d-lagged) =====")
    print(f"{'signal':22s} {'총수익':>8s} {'CAGR':>6s} {'MaxDD':>7s} {'Sharpe':>6s} {'Calmar':>6s} {'최근5wk':>7s}")
    res = []
    for name, exp in configs.items():
        r = ew * exp.shift(1).clip(0,1)
        t,c,d,sh,cal = stats(r)
        rec = r[recent].sum()   # recent ~5wk cumulative (approx)
        res.append((name,t,c,d,sh,cal,rec))
    res.sort(key=lambda x: (-(x[5] if not np.isnan(x[5]) else -9)))   # by Calmar desc
    for name,t,c,d,sh,cal,rec in res:
        cs = f"{cal:.2f}" if not np.isnan(cal) else " n/a"
        print(f"{name:22s} {t:+7.0%} {c:+5.0%} {d:+6.0%} {sh:+5.2f} {cs:>6s} {rec:+6.1%}")

for m in ("KR","US"):
    run(m)
