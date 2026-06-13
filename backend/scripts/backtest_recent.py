"""No-look-ahead virtual trade over the most recent ~2 weeks IN our data.

Our prices end ~2026-06-01, so the "recent 2 weeks" is the last `hold`
trading days of the dataset (we cannot trade beyond data we don't have).

Honest protocol:
  * pick entry date T = `hold` trading days before the last data date,
  * retrain the rank model ONLY on rows whose 21d label is fully realised by
    T (date <= T-21 trading days) -> the model has seen NOTHING at/after T,
  * at T pick the top-decile (BUY) and bottom-decile by predicted rank,
  * hold to the last data date, score realised P&L from actual closes,
  * compare to an equal-weight benchmark of all tickers.

Caveat: ONE short window = a single noisy sample (illustrative, not robust);
the walk-forward IC is the trustworthy edge estimate. Horizon is also short
(~`hold`d) vs the model's 21d target.

Usage:
    uv run python scripts/backtest_recent.py --market US --hold 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import lightgbm as lgb

from core.db import session_scope
from scripts.train_lgbm import ALL_FEATURE_COLS

RANK = "rank_fwd_21d"


def closes_on(session, market: str, d) -> dict[str, float]:
    from sqlalchemy import text
    rows = session.execute(text(
        "SELECT ticker, close FROM daily_prices WHERE market=:m AND trade_date=:d"
    ), {"m": market, "d": d}).all()
    return {t: float(c) for t, c in rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US")
    ap.add_argument("--hold", type=int, default=10, help="trading days held = test window")
    ap.add_argument("--topk", type=int, default=50)
    ap.add_argument("--decile", type=float, default=0.1)
    args = ap.parse_args()

    df = pd.read_parquet(f"var/_fs_ab_{args.market}_365.parquet")
    df["date"] = pd.to_datetime(df["date"])
    dates = np.sort(df["date"].unique())
    last = dates[-1]
    T = dates[-(args.hold + 1)]
    train_cut = dates[max(0, len(dates) - (args.hold + 1) - 21)]  # 21d embargo
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]

    tr = df[(df["date"] <= train_cut)].dropna(subset=[RANK])
    at_T = df[df["date"] == T].copy()
    print(f"[bt] {args.market}  enter T={pd.Timestamp(T).date()}  exit={pd.Timestamp(last).date()} "
          f"(hold {args.hold}d)  train<= {pd.Timestamp(train_cut).date()} "
          f"(n_train={len(tr)}, n_at_T={len(at_T)})", flush=True)

    base = dict(n_estimators=400, num_leaves=31, learning_rate=0.03,
                min_child_samples=100, subsample=0.7, colsample_bytree=0.6,
                reg_lambda=5.0, verbose=-1)
    sel = lgb.LGBMRegressor(**base).fit(tr[feats].astype(float), tr[RANK].astype(float))
    top = pd.Series(sel.feature_importances_, index=feats).sort_values(ascending=False).head(args.topk).index.tolist()
    model = lgb.LGBMRegressor(**base).fit(tr[top].astype(float), tr[RANK].astype(float))

    at_T["score"] = model.predict(at_T[top].astype(float))
    at_T["rank_pct"] = at_T["score"].rank(pct=True)

    with session_scope() as s:
        c_in = closes_on(s, args.market, pd.Timestamp(T).date())
        c_out = closes_on(s, args.market, pd.Timestamp(last).date())
    at_T["c_in"] = at_T["ticker"].map(c_in)
    at_T["c_out"] = at_T["ticker"].map(c_out)
    at_T = at_T.dropna(subset=["c_in", "c_out"])
    at_T = at_T[at_T["c_in"] > 0]
    at_T["realized"] = at_T["c_out"] / at_T["c_in"] - 1.0

    bench = float(at_T["realized"].mean())
    longs = at_T[at_T["rank_pct"] >= 1 - args.decile]
    shorts = at_T[at_T["rank_pct"] <= args.decile]
    long_ret = float(longs["realized"].mean())
    short_ret = float(shorts["realized"].mean())
    hit = float((longs["realized"] > 0).mean())
    hit_vs_bench = float((longs["realized"] > bench).mean())

    print(f"\n===== virtual trade result ({args.market}, {args.hold}d, no look-ahead) =====")
    print(f"  universe tradeable        : {len(at_T)}")
    print(f"  benchmark (equal-weight)  : {bench*100:+.2f}%")
    print(f"  BUY  top-decile ({len(longs)})    : {long_ret*100:+.2f}%   "
          f"(excess {(long_ret-bench)*100:+.2f}%p)")
    print(f"  bottom-decile ({len(shorts)})     : {short_ret*100:+.2f}%")
    print(f"  long-short spread         : {(long_ret-short_ret)*100:+.2f}%p")
    print(f"  BUY hit-rate (>0)         : {hit*100:.0f}%   (>bench {hit_vs_bench*100:.0f}%)")
    print(f"\n  TOP picks @T -> realized:")
    show = longs.sort_values("score", ascending=False).head(8)
    for _, r in show.iterrows():
        print(f"    {r['ticker']:<8} score={r['score']:+.3f}  "
              f"{r['c_in']:,.0f} -> {r['c_out']:,.0f}  ({r['realized']*100:+.1f}%)")


if __name__ == "__main__":
    main()
