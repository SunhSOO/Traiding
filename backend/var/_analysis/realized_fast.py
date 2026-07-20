"""FAST realized KR return — no feature rebuild.
Use the existing _fs_ab_KR cache (features as of ~June 1, full universe, what
the deployed model trained on) to pick the top-decile basket, then measure its
ACTUAL return from that date to 2026-07-08 using the freshly-synced KR prices.
A REAL realized number on genuinely-unseen-forward data (picks pre-date the
prices used to score them)."""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, joblib
from sqlalchemy import text
from core.db import session_scope

fdf = pd.read_parquet("var/_fs_ab_KR_365.parquet")
fdf["date"] = pd.to_datetime(fdf["date"])
pick_date = fdf["date"].max()                       # ~2026-06-01
latest = fdf[fdf["date"] == pick_date].copy()

b = joblib.load("var/models/production_KR.joblib")
fcols = b["feature_cols"]; norm = b.get("normalize", "") or ""
sub = latest.reindex(columns=fcols).astype(float)
X = ((sub - sub.mean()) / (sub.std() + 1e-9)).clip(-3, 3) if norm.startswith("cross_section_zscore") else sub
latest["score"] = b["rank_model"].predict(X)

with session_scope() as s:
    names = {r[0]: r[1] for r in s.execute(text("SELECT ticker,name FROM securities WHERE market='KR'")).all()}
    prows = s.execute(text("SELECT trade_date,ticker,close FROM daily_prices WHERE market='KR' "
        "AND trade_date IN (SELECT DISTINCT trade_date FROM daily_prices WHERE market='KR' "
        "AND trade_date BETWEEN :p AND '2026-07-08')"), {"p": pick_date.date()}).all()
px = pd.DataFrame(prows, columns=["date", "ticker", "close"]); px["date"] = pd.to_datetime(px["date"])
px["close"] = px["close"].astype(float)
cp = px.pivot_table(index="date", columns="ticker", values="close", aggfunc="last").sort_index()
if pick_date not in cp.index:
    pick_date = cp.index[cp.index >= pick_date][0]
end_date = cp.index.max()
ret = (cp.loc[end_date] / cp.loc[pick_date] - 1.0)   # realized return pick→2026-07-08

latest = latest[latest["ticker"].isin(ret.dropna().index)].copy()
latest["realized"] = latest["ticker"].map(ret)
latest["name"] = latest["ticker"].map(names)
latest = latest.sort_values("score", ascending=False)

n = len(latest)
dec = max(1, n // 10)
top = latest.head(dec)
print(f"REALIZED KR :: pick {pick_date.date()} -> {end_date.date()} ({(end_date-pick_date).days}d)  universe n={n}", flush=True)
print(f"  TOP-DECILE basket ({dec} names):  {top['realized'].mean():+.2%}")
print(f"  BENCHMARK (equal-weight all {n}):  {latest['realized'].mean():+.2%}")
print(f"  EXCESS:                            {top['realized'].mean()-latest['realized'].mean():+.2%}")
print(f"  top-quintile ({max(1,n//5)}):       {latest.head(max(1,n//5))['realized'].mean():+.2%}")
print("\n  top-8 picks & their realized return:")
print(top.head(8)[["ticker", "name", "score", "realized"]].to_string(index=False,
      formatters={"realized": "{:+.2%}".format, "score": "{:+.3f}".format}))
