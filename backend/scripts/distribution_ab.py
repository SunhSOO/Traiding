"""Point vs distribution prediction — can we produce a target-price RANGE?

The trading goal needs more than a point estimate: a target price with a
confidence band, plus a directional tilt for sizing. IC ~0.02-0.05 means a
single point 21d-return forecast is too noisy to quote as a number; a
quantile forecast is the honest alternative.

This trains LightGBM quantile models (q10/q50/q90) on ret_fwd_21d with a
chronological train/test split and reports on the held-out test:
  - q50 directional IC + hit (the point-equivalent signal)
  - interval coverage: P(actual in [q10, q90])  (should be ~0.80 if calibrated)
  - median interval width (the target-price band width, in return units)
  - a sizing demo: long top-decile by q50, short bottom-decile, mean fwd ret

Reuses the parquet cached by feature_selection_ab.py (no rebuild).

Usage:
    uv run python scripts/distribution_ab.py --market KR --days 365
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

from scripts.train_lgbm import ALL_FEATURE_COLS

TARGET = "ret_fwd_21d"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR")
    ap.add_argument("--days", type=int, default=365)
    args = ap.parse_args()

    cache = Path(f"var/_fs_ab_{args.market}_{args.days}.parquet")
    if not cache.exists():
        print(f"[dist-ab] cache {cache} missing — run feature_selection_ab.py first")
        return
    df = pd.read_parquet(cache).dropna(subset=[TARGET]).copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]

    # Chronological 80/20 split (no leakage).
    cut = df["date"].quantile(0.8)
    tr, te = df[df["date"] <= cut], df[df["date"] > cut]
    Xtr, ytr = tr[feats].astype(float), tr[TARGET].astype(float)
    Xte, yte = te[feats].astype(float), te[TARGET].astype(float)
    print(f"[dist-ab] {args.market} train={len(tr)} test={len(te)} feats={len(feats)}", flush=True)

    base = dict(n_estimators=400, num_leaves=31, learning_rate=0.03,
                min_child_samples=100, subsample=0.7, colsample_bytree=0.6,
                reg_lambda=5.0, verbose=-1)
    preds = {}
    for q in (0.1, 0.5, 0.9):
        m = lgb.LGBMRegressor(objective="quantile", alpha=q, **base)
        m.fit(Xtr, ytr)
        preds[q] = m.predict(Xte)

    q10, q50, q90 = preds[0.1], preds[0.5], preds[0.9]
    yt = yte.values

    ic = spearmanr(q50, yt).correlation
    hit = float(((q50 > 0) == (yt > 0)).mean())
    coverage = float(((yt >= q10) & (yt <= q90)).mean())
    width = float(np.median(q90 - q10))

    # Sizing demo: decile long/short by q50.
    order = np.argsort(q50)
    n = len(order); d = n // 10
    short_ret = yt[order[:d]].mean()
    long_ret = yt[order[-d:]].mean()

    print("\n===== distribution (quantile) forecast - held-out test =====")
    print(f"  q50 directional IC (spearman) : {ic:.4f}")
    print(f"  q50 hit-rate                  : {hit:.4f}")
    print(f"  interval coverage P(in q10..q90): {coverage:.4f}  (target ~0.80)")
    print(f"  median interval width (ret)   : {width:.4f}  (= target-price band ±{width/2*100:.1f}%)")
    print(f"  decile long fwd-ret           : {long_ret:+.4f}")
    print(f"  decile short fwd-ret          : {short_ret:+.4f}")
    print(f"  long-short spread             : {long_ret-short_ret:+.4f}")
    print("\n  해석: coverage가 0.80 근처면 밴드가 잘 보정됨 → 목표가를")
    print("       'q50 기준 +x% (밴드 [q10,q90])'로 제시 가능. long-short 스프레드>0이면")
    print("       횡단면 분산전략이 실제로 수익 방향.")


if __name__ == "__main__":
    main()
