"""Production model bundle — everything the trading decision needs, using
the configuration the A/B experiments selected:

  * target          = rank_fwd_21d   (cross-sectional; raw return unpredictable)
  * features        = top-K by importance (462->K cuts overfit; KR rank
                      IC 0.062->0.087 at K=50)
  * target price     = LightGBM quantile q10/q50/q90 over ret_fwd_21d
  * honest band      = CQR conformal widening so the 80% band really covers ~80%
  * sizing input     = rank score (diversified, regime-scaled downstream)

Produces one joblib bundle per market with the rank model, the three quantile
models, the conformal Q, the chosen feature list, and held-out metrics. The
decision layer can load it to emit: per-ticker rank, a target price = last*(1+q50),
and a calibrated band [last*(1+q10-Q), last*(1+q90+Q)].

Chronological 70/15/15 train/cal/test (no leakage). Reuses the parquet cached
by feature_selection_ab.py; builds if absent.

Usage:
    uv run python scripts/train_production.py --market KR --days 365 --topk 50
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import lightgbm as lgb
import joblib
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge

from scripts.train_lgbm import ALL_FEATURE_COLS
from training.ensemble_model import EnsembleRankModel

RANK_TARGET = "rank_fwd_21d"
RET_TARGET = "ret_fwd_21d"


def _load_or_build(market: str, days: int) -> pd.DataFrame:
    cache = Path(f"var/_fs_ab_{market}_{days}.parquet")
    if cache.exists():
        print(f"[prod] loading {cache}", flush=True)
        return pd.read_parquet(cache)
    from core.db import session_scope
    from training.features import build_feature_matrix
    from training.features_cross_section import apply_cross_section_features
    from training.labels_multi import attach_labels, load_close_panel
    end = date.today(); start = end - timedelta(days=days)
    print(f"[prod] building {market} {start}..{end}", flush=True)
    with session_scope() as s:
        feat_df, _ = build_feature_matrix(s, market=market, start=start, end=end)
        close = load_close_panel(s, market=market, start=start - timedelta(days=10), end=end)
    feat_df = attach_labels(feat_df, close)
    feat_df = apply_cross_section_features(feat_df)
    feat_df.to_parquet(cache)
    return feat_df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--topk", type=int, default=50)
    ap.add_argument("--alpha", type=float, default=0.2)
    ap.add_argument("--cache", type=str, default=None,
                    help="explicit feature-matrix parquet (e.g. multi-regime 2018-2024)")
    ap.add_argument("--no-normalize", action="store_true",
                    help="disable per-date cross-sectional z-score (validated ON by default)")
    ap.add_argument("--no-sample-weight", action="store_true",
                    help="disable |mn-label| sample weighting (validated ON by default)")
    ap.add_argument("--norm-mode", choices=["zscore", "winsor"], default=None,
                    help="per-date normalization. Default per-market: KR=winsor (clip ±3, "
                         "raises KR IC on full stack), US=zscore.")
    ap.add_argument("--label", choices=["mn", "tb"], default=None,
                    help="training target. Default is per-market VALIDATED choice: US=tb "
                         "(triple-barrier, bundle WF IC 0.031->0.041, 100%% +folds), KR=mn "
                         "(market-neutral; tb hurts KR 0.044->0.033). Override to force.")
    ap.add_argument("--ensemble", dest="ensemble", action="store_true", default=None,
                    help="wrap the rank model as a lgbm+ridge cross-sectional rank-average. "
                         "OPT-IN / OFF by default (both markets): the robust 16-fold walk-forward "
                         "+ adversarial review (2026-07-10) found the US ensemble edge regime-lucky "
                         "(74%% of the gap from 3 bull folds), a wash-to-negative at the median, and "
                         "bear-toxic (ridge IC inverts in stress) — NOT adopted. Kept for shadow eval.")
    ap.add_argument("--no-ensemble", dest="ensemble", action="store_false",
                    help="force pure LightGBM rank model (this is the default).")
    ap.add_argument("--ridge-alpha", type=float, default=10.0,
                    help="Ridge L2 strength for the ensemble branch when --ensemble (default 10.0).")
    args = ap.parse_args()
    if args.label is None:   # per-market validated label (2026-06-20, label-agnostic WF IC)
        args.label = "tb" if args.market == "US" else "mn"
    if args.ensemble is None:   # DEFAULT OFF both markets. The 2026-07-10 robust
        # 16-fold walk-forward + 6-agent adversarial review rejected the US ensemble
        # (regime-lucky / non-replicated risk-adj / bear-toxic). Pure lgbm retained;
        # --ensemble stays available for shadow evaluation only.
        args.ensemble = False
    if args.norm_mode is None:   # zscore both markets. (A/B/C 2026-06-23: KR winsor
        # raised alpha_lab IC but bundle WF IC was a wash 0.0405<0.0440 — harness-
        # specific, not adopted. --norm-mode winsor kept for experimentation.)
        args.norm_mode = "zscore"

    if args.cache:
        print(f"[prod] loading {args.cache}", flush=True)
        df = pd.read_parquet(args.cache)
    else:
        df = _load_or_build(args.market, args.days)
    # Adopt the VALIDATED target: market-neutral residual (ret - date-mean).
    # Multi-seed/multi-regime A/B showed this is the one robust lever
    # (~+10%/yr vs ~0 for rank/raw). Selection + rank model train on it.
    df["date"] = pd.to_datetime(df["date"])
    df["mn_fwd_21d"] = df["ret_fwd_21d"] - df.groupby("date")["ret_fwd_21d"].transform("mean")
    if args.label == "tb":
        # VALIDATED (2026-06-20): triple-barrier path-aware label (return at first
        # touch of ±k·vol·sqrt(H), mn-neutralized) raised OOS IC on BOTH markets
        # (US 0.0356->0.0402, KR 0.0109->0.0122) with lower concentration & MDD.
        # Label-only change — inference path/model output unchanged.
        from scripts.alpha_lab import _tb_label, _close_panel
        px = _close_panel(args.market, df["date"].min().date(), df["date"].max().date())
        df["tbmn"] = _tb_label(df, px)
        target = "tbmn"
    else:
        target = "mn_fwd_21d"
    df = df.dropna(subset=[target, RET_TARGET]).copy()
    df = df.sort_values("date")
    feats_all = [c for c in ALL_FEATURE_COLS if c in df.columns]

    # VALIDATED preprocessing: per-date cross-sectional z-score of features.
    # A/B (2026-06-17): mn_norm beat raw mn_rs on 3/4 market×step cuts, stayed
    # positive in bear (US hi-vix +0.80 vs mn_rs -0.94), cut MDD (-22 vs -34%),
    # kept IC (=> stabilization, not factor tilt). Applied to ALL feature models;
    # inference reproduces it across the daily cross-section (point-in-time safe).
    normalize = not args.no_normalize
    if normalize:
        g = df.groupby("date")
        df[feats_all] = ((df[feats_all] - g[feats_all].transform("mean"))
                         / (g[feats_all].transform("std") + 1e-9))
        # per-market normalization mode (A/B/C synergy check 2026-06-23): on the
        # FULL KR stack, winsorizing (clip ±3) the z-scores RAISES OOS IC
        # (0.011->0.023) & cuts MDD; on US it hurts → KR=winsor, US=zscore.
        if args.norm_mode == "winsor":
            df[feats_all] = df[feats_all].clip(-3, 3)
        print(f"[prod] per-date z-score ({args.norm_mode}) applied to {len(feats_all)} feats", flush=True)

    q1, q2 = df["date"].quantile(0.7), df["date"].quantile(0.85)
    tr = df[df["date"] <= q1]
    cal = df[(df["date"] > q1) & (df["date"] <= q2)]
    te = df[df["date"] > q2]
    print(f"[prod] {args.market} train={len(tr)} cal={len(cal)} test={len(te)} "
          f"feats={len(feats_all)}", flush=True)

    base = dict(n_estimators=500, num_leaves=31, learning_rate=0.03,
                min_child_samples=100, subsample=0.7, colsample_bytree=0.6,
                reg_lambda=5.0, verbose=-1)

    # VALIDATED sample weighting: weight by |mn label| (focus on big movers).
    # A/B (2026-06-19): raised OOS rank-IC on BOTH markets (US 0.0278->0.0356,
    # KR 0.0071->0.0109) with better US IC+%/concentration & bear behaviour.
    sw_on = not args.no_sample_weight
    sw_all = np.abs(tr[target].values) if sw_on else None
    sw_full = np.abs(df[target].values) if sw_on else None

    # 1) Feature selection: fit a rank model on the train slice, take top-K.
    sel = lgb.LGBMRegressor(**base)
    sel.fit(tr[feats_all].astype(float), tr[target].astype(float), sample_weight=sw_all)
    imp = pd.Series(sel.feature_importances_, index=feats_all).sort_values(ascending=False)
    topk = imp.head(args.topk).index.tolist()

    # 2) HONEST metric = expanding-window walk-forward IC (live-like: always
    #    train on the past, test on the next block). A single train/test split
    #    is too noisy here (one window gave -0.02, another +0.28); the walk-
    #    forward mean is the trustworthy expected edge.
    dts = np.sort(df["date"].unique())
    blocks = np.array_split(dts, 8)
    wf_ics, wf_ls = [], []
    for i in range(2, 8):
        cut_tr = blocks[i - 1].max()
        wtr = df[df["date"] <= cut_tr]
        wte = df[(df["date"] > cut_tr) & (df["date"] <= blocks[i].max())]
        if len(wte) < 200:
            continue
        wsw = np.abs(wtr[target].values) if sw_on else None
        m = lgb.LGBMRegressor(**base)
        m.fit(wtr[topk].astype(float), wtr[target].astype(float), sample_weight=wsw)
        p = m.predict(wte[topk].astype(float))
        if args.ensemble:   # report the IC of what actually ships (lgbm+ridge)
            rg = Ridge(alpha=args.ridge_alpha).fit(
                EnsembleRankModel._prep(wtr[topk].astype(float)),
                wtr[target].astype(float), sample_weight=wsw)
            pr = rg.predict(EnsembleRankModel._prep(wte[topk].astype(float)))
            p = pd.Series(p).rank().values + pd.Series(pr).rank().values
        # IC reference = realized market-neutral return (label-agnostic, tradeable),
        # NOT the training target — so the metric stays comparable across label choices.
        wf_ics.append(spearmanr(p, wte["mn_fwd_21d"].values).correlation)
        yr = wte[RET_TARGET].values; o = np.argsort(p); d = max(len(o)//10, 1)
        wf_ls.append(float(yr[o[-d:]].mean() - yr[o[:d]].mean()))
    wf_ics = np.array(wf_ics); wf_ls = np.array(wf_ls)
    rank_ic = float(wf_ics.mean()); rank_ic_std = float(wf_ics.std())
    pos_frac = float((wf_ics > 0).mean())
    ls = float(wf_ls.mean())

    # 3) Final deployable rank model on ALL data (latest info included).
    rank_model = lgb.LGBMRegressor(**base)
    rank_model.fit(df[topk].astype(float), df[target].astype(float), sample_weight=sw_full)
    if args.ensemble:
        # US: rank-average with a linear Ridge — validated to add cross-sectional
        # excess the tree misses (var/_analysis/excess_split.py). KR stays pure lgbm.
        ridge = Ridge(alpha=args.ridge_alpha)
        ridge.fit(EnsembleRankModel._prep(df[topk].astype(float)),
                  df[target].astype(float), sample_weight=sw_full)
        rank_model = EnsembleRankModel(rank_model, ridge)
        print(f"[prod] rank model = ENSEMBLE lgbm+ridge(alpha={args.ridge_alpha})", flush=True)
    else:
        print("[prod] rank model = LightGBM (pure)", flush=True)

    # 4) Quantile models for target price + CQR conformal Q.
    qmodels = {}
    for q in (args.alpha/2, 0.5, 1-args.alpha/2):
        m = lgb.LGBMRegressor(objective="quantile", alpha=q, **base)
        m.fit(tr[topk].astype(float), tr[RET_TARGET].astype(float))
        qmodels[q] = m
    lo_q, hi_q = args.alpha/2, 1-args.alpha/2
    clo = qmodels[lo_q].predict(cal[topk].astype(float))
    chi = qmodels[hi_q].predict(cal[topk].astype(float))
    yc = cal[RET_TARGET].values
    E = np.maximum(clo - yc, yc - chi)
    n = len(E); k = min(max(int(np.ceil((n+1)*(1-args.alpha))), 1), n)
    Q = float(np.sort(E)[k-1])
    tlo = qmodels[lo_q].predict(te[topk].astype(float))
    thi = qmodels[hi_q].predict(te[topk].astype(float))
    yt_ret = te[RET_TARGET].values
    cov = float(((yt_ret >= tlo - Q) & (yt_ret <= thi + Q)).mean())

    bundle = {
        "market": args.market, "target": target,
        "normalize": ("cross_section_zscore" + ("_winsor" if args.norm_mode == "winsor" else "")) if normalize else None,
        "sample_weight": "abs_mn_label" if sw_on else None,
        "label": args.label,
        "rank_model_kind": ("ensemble_lgbm_ridge" if args.ensemble else "lgbm"),
        "ridge_alpha": (args.ridge_alpha if args.ensemble else None),
        "feature_cols": topk, "rank_model": rank_model,
        "quantile_models": qmodels, "conformal_Q": Q, "alpha": args.alpha,
        "metrics": {"rank_ic_walkfwd_mean": rank_ic, "rank_ic_walkfwd_std": rank_ic_std,
                     "rank_ic_positive_frac": pos_frac, "decile_long_short_mean": ls,
                     "band_coverage_test": cov, "n_rows": len(df)},
        "built": str(date.today()),
    }
    out = Path(f"var/models/production_{args.market}.joblib")
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out)

    print(f"\n===== production bundle: {out} =====")
    print(f"  features (top{args.topk}): {topk[:8]} ...")
    print(f"  rank IC walk-forward  : mean {rank_ic:+.4f} std {rank_ic_std:.4f} "
          f"positive {pos_frac*100:.0f}%")
    print(f"  decile long-short(WF) : {ls:+.4f}")
    print(f"  band coverage (test)  : {cov:.3f} (target {1-args.alpha:.2f})")
    print(f"  conformal Q           : {Q:+.4f}")
    print(f"  final model trained on all {len(df)} rows. saved.")


if __name__ == "__main__":
    main()
