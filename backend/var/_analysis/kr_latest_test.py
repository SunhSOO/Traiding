"""#3 — the deployed KR model on the LATEST (2026-07-08) real KR data.
Directional read: what does the machine rank/pick TODAY on a representative
KR sample. (Optional-module stubs for speed; KR's key features — fundamental/
technical/macro/beta/amihud — remain intact.) Paper/directional, not real money."""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, joblib
from datetime import date
from sqlalchemy import text
from core.db import session_scope

def _stub(*a, **k):
    for x in a:
        if isinstance(x, pd.DatetimeIndex): return pd.DataFrame(index=x)
        idx = getattr(x, "index", None)
        if isinstance(idx, pd.DatetimeIndex): return pd.DataFrame(index=idx)
    return pd.DataFrame()
import training.features_embeddings as _e; _e.compute_embedding_features = _stub
import training.features_finbert_agg as _f; _f.compute_finbert_features = _stub
import training.features_gdelt as _g; _g.compute_gdelt_features = _stub
import training.features_information_v2 as _i; _i.compute_information_v2 = _stub
from training.features import build_feature_matrix

N = 30
with session_scope() as s:
    rows = s.execute(text(
        "SELECT ticker, name FROM securities WHERE market='KR' AND is_active ORDER BY ticker")).all()
    names = {r[0]: r[1] for r in rows}
    tks = [r[0] for r in rows]
    if len(tks) > N:
        step = len(tks)//N; tks = tks[::step][:N]   # even-stride sample
    print(f"building {len(tks)} KR tickers...", flush=True)
    import os
    _cache = "var/_kr_latest_feat.parquet"
    if os.path.exists(_cache):
        fdf = pd.read_parquet(_cache)
        class R: feature_columns = fdf.shape[1]
        rep = R()
    else:
        fdf, rep = build_feature_matrix(s, market="KR", start=date(2026, 6, 5), end=date(2026, 7, 8), tickers=tks)
        fdf.to_parquet(_cache)

fdf["date"] = pd.to_datetime(fdf["date"])
last_d = fdf["date"].max()
latest = fdf[fdf["date"] == last_d].copy()
print(f"KR latest-data test :: date={last_d.date()}  stocks={len(latest)}  feats={rep.feature_columns}", flush=True)

b = joblib.load("var/models/production_KR.joblib")
fcols = b["feature_cols"]                       # ALL 50 the model expects
present = [c for c in fcols if c in latest.columns]
sub = latest.reindex(columns=fcols).astype(float)   # missing (stubbed) → NaN
norm = b.get("normalize", "") or ""
if norm.startswith("cross_section_zscore"):
    X = (sub - sub.mean()) / (sub.std() + 1e-9)     # NaN cols stay NaN (tree-tolerated)
    if norm.endswith("_winsor"): X = X.clip(-3, 3)
else:
    X = sub
latest["score"] = b["rank_model"].predict(X)
latest["pred_ret"] = b["quantile_models"][0.5].predict(X) if "quantile_models" in b else np.nan
latest["rank_pct"] = latest["score"].rank(pct=True)
latest = latest.sort_values("score", ascending=False)
latest["name"] = latest["ticker"].map(names)

print(f"model features present: {len(present)}/{len(fcols)} (stubbed {len(fcols)-len(present)} → NaN, tree-tolerated)", flush=True)
print("\n=== TOP-10 KR picks on LATEST data (rank basket = BUY candidates) ===", flush=True)
print(latest.head(10)[["ticker", "name", "score", "pred_ret", "rank_pct"]].to_string(index=False))
print("\n=== BOTTOM-5 (SELL/avoid) ===", flush=True)
print(latest.tail(5)[["ticker", "name", "score", "pred_ret"]].to_string(index=False))
