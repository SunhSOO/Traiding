"""Conformalized Quantile Regression (CQR) — make target-price bands honest.

distribution_ab showed the raw q10/q90 band covers only ~64% of outcomes
(target 80%) — overconfident, so a quoted "target price ± band" would lie.
CQR (Romano, Patterson, Candès 2019) fixes this with a finite-sample
coverage guarantee:

  1. fit q_lo/q_hi on TRAIN,
  2. on a held-out CALIBRATION set compute conformity scores
     E_i = max(q_lo(x_i) - y_i, y_i - q_hi(x_i)),
  3. Q = the ceil((n+1)(1-alpha))/n empirical quantile of E,
  4. calibrated band = [q_lo(x) - Q, q_hi(x) + Q].

Chronological splits (60/20/20 train/cal/test) so there's no leakage.
Reuses the parquet cached by feature_selection_ab.py.

Usage:
    uv run python scripts/conformal_ab.py --market KR --days 365 --alpha 0.2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import lightgbm as lgb

from scripts.train_lgbm import ALL_FEATURE_COLS

TARGET = "ret_fwd_21d"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--alpha", type=float, default=0.2, help="miscoverage (0.2 -> 80% band)")
    args = ap.parse_args()

    cache = Path(f"var/_fs_ab_{args.market}_{args.days}.parquet")
    if not cache.exists():
        print(f"[cqr] cache {cache} missing - run feature_selection_ab.py first")
        return
    df = pd.read_parquet(cache).dropna(subset=[TARGET]).copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]

    # Chronological 60/20/20 train / calibration / test.
    q1, q2 = df["date"].quantile(0.6), df["date"].quantile(0.8)
    tr = df[df["date"] <= q1]
    cal = df[(df["date"] > q1) & (df["date"] <= q2)]
    te = df[df["date"] > q2]
    print(f"[cqr] {args.market} train={len(tr)} cal={len(cal)} test={len(te)} "
          f"alpha={args.alpha} (target {int((1-args.alpha)*100)}% band)", flush=True)

    lo_q, hi_q = args.alpha / 2, 1 - args.alpha / 2  # e.g. 0.1 / 0.9
    base = dict(n_estimators=400, num_leaves=31, learning_rate=0.03,
                min_child_samples=100, subsample=0.7, colsample_bytree=0.6,
                reg_lambda=5.0, verbose=-1)

    def fit_q(q):
        m = lgb.LGBMRegressor(objective="quantile", alpha=q, **base)
        m.fit(tr[feats].astype(float), tr[TARGET].astype(float))
        return m

    m_lo, m_hi = fit_q(lo_q), fit_q(hi_q)

    def band(X):
        return m_lo.predict(X), m_hi.predict(X)

    # Calibration conformity scores.
    clo, chi = band(cal[feats].astype(float))
    yc = cal[TARGET].values
    E = np.maximum(clo - yc, yc - chi)
    n = len(E)
    k = int(np.ceil((n + 1) * (1 - args.alpha)))
    k = min(max(k, 1), n)
    Q = np.sort(E)[k - 1]

    # Test coverage: raw vs CQR.
    tlo, thi = band(te[feats].astype(float))
    yt = te[TARGET].values
    cov_raw = float(((yt >= tlo) & (yt <= thi)).mean())
    w_raw = float(np.median(thi - tlo))
    cov_cqr = float(((yt >= tlo - Q) & (yt <= thi + Q)).mean())
    w_cqr = float(np.median((thi + Q) - (tlo - Q)))

    print(f"\n===== CQR target-price band ({int((1-args.alpha)*100)}%) =====")
    print(f"  conformity Q (widen each side): {Q:+.4f}")
    print(f"  RAW  : coverage {cov_raw:.3f}  width {w_raw:.3f} (±{w_raw/2*100:.1f}%)")
    print(f"  CQR  : coverage {cov_cqr:.3f}  width {w_cqr:.3f} (±{w_cqr/2*100:.1f}%)")
    ok = abs(cov_cqr - (1 - args.alpha)) <= 0.05
    print(f"  => CQR coverage {'OK (목표±5%p 내)' if ok else 'still off'}; "
          f"밴드를 q50 기준 ±{w_cqr/2*100:.1f}% 로 제시 가능.")


if __name__ == "__main__":
    main()
