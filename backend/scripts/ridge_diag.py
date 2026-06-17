"""Ridge alpha diagnostic — is the normalized-linear (mn_ridge) edge REAL or a LEAK?

mn_ridge (per-date z-score -> LGBM top50 -> Ridge(10)) showed US +20.9%/yr,
2x the GBDT baseline. Linear >> trees on identical features is abnormal, so we
interrogate the mechanism before believing it:

  1. OOS IC   : spearman(pred, realized mn_fwd) averaged over folds. A real ~20%
                alpha needs a genuine, stable positive IC. Leak => implausibly high.
  2. Top |coef*1| features: which signals drive the linear bet. A sensible factor
                (momentum/value/reversal) => plausible. A feature that *shouldn't*
                predict (or is contemporaneous with the label) => leak suspect.
  3. Per-fold long-decile alpha: is the +20% spread across folds, or concentrated
                in a few lucky rebalances (artifact)?
  4. Condition number of z-scored Xtr: confirm it's NOT the rcond~1e-36 degeneracy
                that sank mn_ridge_raw.

Usage: uv run python scripts/ridge_diag.py --market US
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sqlalchemy import text
from core.db import session_scope
from scripts.train_lgbm import ALL_FEATURE_COLS

PERIOD = "2018-01-01_2024-01-01"
BASE = dict(n_estimators=300, num_leaves=31, learning_rate=0.04, min_child_samples=100,
            subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, verbose=-1, n_jobs=-1)


def _px(market, lo, hi):
    with session_scope() as s:
        rows = s.execute(text("SELECT trade_date,ticker,close FROM daily_prices "
                              "WHERE market=:m AND trade_date BETWEEN :a AND :b"),
                         {"m": market, "a": lo, "b": hi}).all()
    p = pd.DataFrame(rows, columns=["date", "ticker", "close"]); p["date"] = pd.to_datetime(p["date"])
    return p.pivot_table(index="date", columns="ticker", values="close", aggfunc="first")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--market", default="US")
    ap.add_argument("--step", type=int, default=42); ap.add_argument("--alpha", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=42); a = ap.parse_args()

    df = pd.read_parquet(Path(f"var/_bt_period_{a.market}_{PERIOD}.parquet"))
    df["date"] = pd.to_datetime(df["date"])
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    df["mn"] = df["ret_fwd_21d"] - df.groupby("date")["ret_fwd_21d"].transform("mean")
    # per-date cross-sectional z-score (point-in-time safe)
    g = df.groupby("date")
    z = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
    z.columns = [c + "_z" for c in feats]
    df = pd.concat([df, z], axis=1)
    zf = list(z.columns)

    dates = np.sort(df["date"].unique())
    px = _px(a.market, pd.Timestamp(dates[0]).date(), pd.Timestamp(dates[-1]).date())
    cost = 0.003 if a.market == "KR" else 0.001
    rebal = list(range(63, len(dates) - 1, a.step))
    B = dict(BASE); B["random_state"] = a.seed

    ics, fold_alpha, conds, coef_acc = [], [], [], {}
    cash = bench = 1.0
    for j, i in enumerate(rebal):
        R = dates[i]; E = dates[rebal[j + 1]] if j + 1 < len(rebal) else dates[-1]
        cut = dates[max(0, i - 21)]
        tr = df[df["date"] <= cut].dropna(subset=["mn"]); atR = df[df["date"] == R].copy()
        if len(tr) < 500 or atR.empty:
            continue
        sel = lgb.LGBMRegressor(**B).fit(tr[zf].astype(float), tr["mn"].astype(float))
        top = pd.Series(sel.feature_importances_, index=zf).sort_values(ascending=False).head(50).index.tolist()
        Xtr = tr[top].astype(float).fillna(0.0).to_numpy()
        rm = Ridge(alpha=a.alpha).fit(Xtr, tr["mn"].astype(float).to_numpy())
        conds.append(np.linalg.cond(Xtr.T @ Xtr + a.alpha * np.eye(len(top))))
        Xte = atR[top].astype(float).fillna(0.0).to_numpy()
        atR["score"] = rm.predict(Xte)
        for f, c in zip(top, rm.coef_):
            coef_acc[f] = coef_acc.get(f, 0.0) + abs(c)
        real = atR["mn"].to_numpy()
        msk = np.isfinite(real) & np.isfinite(atR["score"].to_numpy())
        if msk.sum() > 10:
            ics.append(spearmanr(atR["score"].to_numpy()[msk], real[msk]).correlation)
        atR["pct"] = atR["score"].rank(pct=True)
        longs = atR[atR["pct"] >= 0.9]["ticker"].tolist()

        def ret(tks):
            r = [px.at[E, t] / px.at[R, t] - 1 for t in tks
                 if t in px.columns and R in px.index and E in px.index
                 and pd.notna(px.at[R, t]) and pd.notna(px.at[E, t]) and px.at[R, t] > 0]
            return float(np.mean(r)) if r else 0.0
        port = ret(longs) - cost; allr = ret(list(px.columns))
        fold_alpha.append(port - allr); cash *= (1 + port); bench *= (1 + allr)

    ny = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[63])).days / 365.25
    fa = np.array(fold_alpha)
    print(f"\n=== {a.market} mn_ridge DIAG (step{a.step} alpha{a.alpha}) ===")
    print(f"alpha/yr        : {(cash - bench) / ny * 100:+.2f}%/yr  (n_folds={len(fa)})")
    print(f"OOS IC          : {np.nanmean(ics):+.4f} ± {np.nanstd(ics):.4f}  (pos folds {np.mean(np.array(ics) > 0) * 100:.0f}%)")
    print(f"per-fold alpha  : mean {fa.mean() * 100:+.2f}%  median {np.median(fa) * 100:+.2f}%  pos {np.mean(fa > 0) * 100:.0f}%")
    print(f"  top5 folds contribute {np.sort(fa)[-5:].sum() / fa.sum() * 100:.0f}% of total (concentration)")
    print(f"cond(X'X+aI)    : median {np.median(conds):.2e}  max {np.max(conds):.2e}  (mn_ridge_raw was ~1e36)")
    print("top |coef| feats:")
    for f, c in sorted(coef_acc.items(), key=lambda kv: -kv[1])[:12]:
        print(f"   {f:<28} {c / len(fa):.4f}")


if __name__ == "__main__":
    main()
