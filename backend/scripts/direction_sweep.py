"""Comprehensive market-direction model sweep — can we make KR predictable?

Attacks the KR bottleneck (direction-gated concentrate fails there). Sweeps
model type (regression / classification) × feature group (all / trend / macro /
momentum / combos) in ONE market-frame build, reporting honest walk-forward OOS
directional accuracy + IC per variant, per market. If any variant lifts KR's
accuracy meaningfully above baseline, the KR gate can be resurrected.

Usage: uv run python scripts/direction_sweep.py --market KR
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

from decision.market_direction import build_market_frame

GROUPS = {
    "all": lambda c: True,
    "trend": lambda c: any(k in c for k in ("sma", "supertrend", "stl", "macd")),
    "macro": lambda c: any(k in c for k in ("vix", "credit", "dxy", "regime", "usdkrw")),
    "mom": lambda c: c.endswith("_mkt"),
    "trend+mom": lambda c: any(k in c for k in ("sma", "supertrend", "stl", "macd")) or c.endswith("_mkt"),
    "macro+trend": lambda c: any(k in c for k in ("vix", "credit", "dxy", "regime", "usdkrw", "sma", "supertrend", "stl", "macd")),
}


def _eval(mf, feats, *, mode, embargo=21, min_train=252, refit=21):
    preds, acts = [], []
    model = None; last = -10**9
    for i in range(min_train + embargo, len(mf)):
        cut = i - embargo
        tr = mf.iloc[:cut].dropna(subset=["fwd"])
        if len(tr) < min_train:
            continue
        if model is None or (i - last) >= refit:
            y = (tr["fwd"] > 0).astype(int) if mode == "clf" else tr["fwd"].astype(float)
            Model = lgb.LGBMClassifier if mode == "clf" else lgb.LGBMRegressor
            model = Model(n_estimators=200, num_leaves=15, learning_rate=0.03,
                          min_child_samples=20, subsample=0.8, colsample_bytree=0.7,
                          reg_lambda=5.0, verbose=-1).fit(tr[feats].astype(float), y)
            last = i
        x = mf.iloc[[i]][feats].astype(float)
        p = (model.predict_proba(x)[0, 1] - 0.5) if mode == "clf" else model.predict(x)[0]
        preds.append(float(p)); acts.append(float(mf.iloc[i]["fwd"]))
    p, a = np.array(preds), np.array(acts)
    no = np.arange(0, len(p), embargo)          # non-overlapping test points
    pn, an = p[no], a[no]
    acc = float(((pn > 0) == (an > 0)).mean())
    base = float((an > 0).mean())
    rho = spearmanr(pn, an).correlation
    up = an[pn > 0].mean() if (pn > 0).any() else float("nan")
    dn = an[pn <= 0].mean() if (pn <= 0).any() else float("nan")
    return acc, base, rho, up, dn, len(pn)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR", choices=["KR", "US"])
    ap.add_argument("--cache", default=None)
    args = ap.parse_args()
    cache = args.cache or f"var/_bt_period_{args.market}_2018-01-01_2024-01-01.parquet"
    mf = build_market_frame(pd.read_parquet(cache))
    allf = [c for c in mf.columns if c not in ("date", "fwd")]
    print(f"[dir-sweep] {args.market}: {len(mf)} dates, {len(allf)} features\n", flush=True)
    print(f"  {'mode/features':<22} {'acc':>5} {'base':>5} {'IC':>6}  {'up-call':>7} {'dn-call':>7}  n")
    rows = []
    for mode in ("reg", "clf"):
        for gname, gfn in GROUPS.items():
            feats = [c for c in allf if gfn(c)]
            if not feats:
                continue
            acc, base, rho, up, dn, n = _eval(mf, feats, mode=mode)
            sep = (up - dn) if np.isfinite(up) and np.isfinite(dn) else float("nan")
            rows.append((sep, mode, gname, acc, base, rho, up, dn, n))
            print(f"  {mode}/{gname:<17} {acc*100:>4.0f}% {base*100:>4.0f}% {rho:>+6.3f}  "
                  f"{up*100:>+6.2f}% {dn*100:>+6.2f}%  {n}", flush=True)
    rows.sort(reverse=True)
    print(f"\n  best by up-vs-down separation: {rows[0][1]}/{rows[0][2]} "
          f"(sep {rows[0][0]*100:+.2f}%p, acc {rows[0][3]*100:.0f}% vs base {rows[0][4]*100:.0f}%)")


if __name__ == "__main__":
    main()
