"""Serious test: can we predict the FORWARD market direction?

The regime-adaptive merge (and a smarter exposure overlay) need a forward
market-direction signal. Earlier I dismissed this after testing ONLY raw price
breadth — a crude single feature. This trains a dedicated market-timing model
on the FULL standard signal set (VIX + credit spread + DXY + breadth + trend +
momentum + regime-classifier output) and measures honest walk-forward skill.

Per date we build ONE market-level feature vector (macro fields are constant
across tickers → take the value; breadth/trend/momentum → cross-sectional
aggregate) and predict the forward 21d market return (universe mean). Walk-
forward (train on the past only); evaluated on NON-overlapping 21d test points
so overlapping-window autocorrelation can't inflate the score.

Reports: directional accuracy vs always-up baseline, Spearman IC, and a simple
timing strategy (invest when predicted up, else cash) vs buy-and-hold.

Usage: uv run python scripts/market_direction_test.py --market US
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.stats import spearmanr

RET = "ret_fwd_21d"

# macro fields (constant across tickers on a date) → mean == the value
MACRO = ["vix", "vix_5d_chg", "vix_21d_chg", "vix_pctile_252d", "ix_vix_mom",
         "hy_credit_spread", "hy_credit_5d_chg", "hy_credit_21d_chg",
         "dxy_5d_chg", "dxy_21d_chg", "usdkrw_21d_chg",
         "regime_risk_on", "regime_risk_off", "regime_neutral", "regime_calm_bull", "regime_conf"]
# cross-sectional breadth/trend (fraction or mean across the universe)
BREADTH = ["px_vs_sma200", "px_vs_sma50", "sma50_above_sma200", "macd_above",
           "supertrend_dir", "stl_trend_strength"]
# trailing market momentum (universe mean)
MOM = ["ret_21d", "ret_63d", "ret_126d", "ret_252d"]


def build_market_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    rows = []
    for d, g in df.groupby("date"):
        r = {"date": d, "fwd": pd.to_numeric(g[RET], errors="coerce").mean()}
        for c in MACRO:
            if c in g.columns:
                r[c] = pd.to_numeric(g[c], errors="coerce").mean()
        for c in BREADTH:
            if c in g.columns:
                v = pd.to_numeric(g[c], errors="coerce")
                r[f"{c}_frac"] = float((v > 0).mean())
                r[f"{c}_mean"] = float(v.mean())
        for c in MOM:
            if c in g.columns:
                r[f"{c}_mkt"] = pd.to_numeric(g[c], errors="coerce").mean()
        rows.append(r)
    m = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return m.dropna(subset=["fwd"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["KR", "US"])
    ap.add_argument("--cache", default=None)
    ap.add_argument("--embargo", type=int, default=21)
    ap.add_argument("--min-train", type=int, default=252)
    ap.add_argument("--refit", type=int, default=21, help="retrain every N days (reuse between)")
    args = ap.parse_args()

    cache = args.cache or f"var/_bt_period_{args.market}_2018-01-01_2024-01-01.parquet"
    m = build_market_frame(pd.read_parquet(cache))
    feats = [c for c in m.columns if c not in ("date", "fwd")]
    print(f"[dir] {args.market} market frame: {len(m)} dates, {len(feats)} features", flush=True)

    params = dict(n_estimators=200, num_leaves=15, learning_rate=0.03,
                  min_child_samples=20, subsample=0.8, colsample_bytree=0.7,
                  reg_lambda=5.0, verbose=-1)

    # Walk-forward daily predictions; evaluate on non-overlapping 21d test points.
    preds, actuals, dates = [], [], []
    idxs = list(range(args.min_train + args.embargo, len(m)))
    model = None; last_fit = -10**9
    for i in idxs:
        cut = i - args.embargo
        tr = m.iloc[:cut]
        if len(tr) < args.min_train:
            continue
        if model is None or (i - last_fit) >= args.refit:   # refit periodically, reuse between
            model = lgb.LGBMRegressor(**params).fit(tr[feats].astype(float), tr["fwd"].astype(float))
            last_fit = i
        preds.append(float(model.predict(m.iloc[[i]][feats].astype(float))[0]))
        actuals.append(float(m.iloc[i]["fwd"]))
        dates.append(m.iloc[i]["date"])

    p = np.array(preds); a = np.array(actuals)
    # non-overlapping eval points (every 21 trading days)
    no = np.arange(0, len(p), args.embargo)
    pn, an = p[no], a[no]

    base_up = float((an > 0).mean())
    acc = float(((pn > 0) == (an > 0)).mean())
    rho = spearmanr(pn, an).correlation
    # timing strategy: invest when predicted up, else cash (0). vs always-invest.
    strat = np.where(pn > 0, an, 0.0)
    bh = an
    def _sh(x):
        return float(np.mean(x) / (np.std(x, ddof=1) + 1e-9) * np.sqrt(252/args.embargo))

    print(f"\n===== MARKET DIRECTION ({args.market}, {len(pn)} non-overlap test windows) =====")
    print(f"  Spearman IC (pred vs fwd)   : {rho:+.3f}")
    print(f"  directional accuracy        : {acc*100:.0f}%   (always-up baseline {base_up*100:.0f}%)")
    print(f"  up-window  pred mean fwd    : {a[no][pn>0].mean()*100:+.2f}%  (n={int((pn>0).sum())})")
    print(f"  down-called pred mean fwd   : {a[no][pn<=0].mean()*100:+.2f}%  (n={int((pn<=0).sum())})")
    print(f"  timing strat  mean/win {np.mean(strat)*100:+.2f}%  Sharpe {_sh(strat):+.2f}  invested {(pn>0).mean()*100:.0f}%")
    print(f"  buy&hold      mean/win {np.mean(bh)*100:+.2f}%  Sharpe {_sh(bh):+.2f}")
    # feature importance from a full-sample fit (diagnostic only)
    fm = lgb.LGBMRegressor(**params).fit(m[feats].astype(float), m["fwd"].astype(float))
    imp = pd.Series(fm.feature_importances_, index=feats).sort_values(ascending=False)
    print(f"  top signals: {list(imp.head(8).index)}")


if __name__ == "__main__":
    main()
