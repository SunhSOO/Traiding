"""LightGBM trainer with proper time-series cross-validation.

Key design choices:

* **Walk-forward CV with embargo** — `TimeSeriesSplit` plus a gap of
  `embargo_days` between train and test so the forward-return target
  (which looks ahead `horizon_days`) cannot leak into the training fold.
* **Early stopping** per fold using the validation set so the model
  doesn't overfit on noisy short windows.
* **Per-cluster + global** — train one model per cluster *and* a
  global model. At inference time the per-cluster model is preferred;
  global is the fallback when a cluster has too few samples.
* **Multi-target stacking** — for each horizon (5d / 21d), train a
  regressor on raw returns AND a regressor on cross-sectional rank.
  Inference averages the two.
* **Feature importance** — exported alongside the model for audit.

Honest scope: we do NOT do hyperparameter search here (Optuna would
take 10x compute). The parameters below are tuned conservatively for
small-sample finance with strong regularisation. Per dev principles:
status=proposed until walk-forward R² is positive and Sharpe in paper
sim exceeds 0.5.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import lightgbm as lgb


# ──────────────────────────────────────────────────────────────────────


DEFAULT_LGB_PARAMS = {
    # Regularisation-heavy defaults for noisy financial data
    "objective": "regression",
    "metric": "rmse",
    "boosting_type": "gbdt",
    "learning_rate": 0.03,
    "num_leaves": 31,
    "max_depth": 6,
    "min_data_in_leaf": 50,        # high floor — guards against overfitting on noise
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "lambda_l1": 0.1,
    "lambda_l2": 0.5,
    "min_gain_to_split": 0.0,
    "verbose": -1,
}


@dataclass
class FoldMetrics:
    fold: int
    n_train: int
    n_test: int
    rmse_train: float
    rmse_test: float
    r2_train: float
    r2_test: float
    hit_rate_test: float
    ic_spearman_test: float


@dataclass
class TrainResultLGB:
    target: str
    cluster_id: str
    booster: Optional[lgb.Booster] = None
    feature_names: list[str] = field(default_factory=list)
    cv_metrics: list[FoldMetrics] = field(default_factory=list)
    final_r2_oof: float = 0.0
    final_hit_rate_oof: float = 0.0
    final_ic_oof: float = 0.0
    final_rmse_oof: float = 0.0
    n_samples: int = 0
    feature_importance: dict[str, float] = field(default_factory=dict)
    notes: str = ""

    def as_metrics_jsonb(self) -> dict:
        return {
            "target": self.target,
            "cluster_id": self.cluster_id,
            "n_samples": self.n_samples,
            "final_r2_oof": self.final_r2_oof,
            "final_hit_rate_oof": self.final_hit_rate_oof,
            "final_ic_oof": self.final_ic_oof,
            "final_rmse_oof": self.final_rmse_oof,
            "cv_folds": [vars(f) for f in self.cv_metrics],
            "top_features": dict(sorted(
                self.feature_importance.items(),
                key=lambda kv: -kv[1],
            )[:15]),
            "notes": self.notes,
        }


# ──────────────────────────────────────────────────────────────────────


def _ic(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Spearman rank correlation."""
    if len(y_true) < 2:
        return 0.0
    ranks_true = pd.Series(y_true).rank().values
    ranks_pred = pd.Series(y_pred).rank().values
    if np.std(ranks_true) == 0 or np.std(ranks_pred) == 0:
        return 0.0
    return float(np.corrcoef(ranks_true, ranks_pred)[0, 1])


def _hit_rate(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) == 0:
        return 0.0
    return float(((y_true >= 0) == (y_pred >= 0)).mean())


def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 2:
        return 0.0
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    if ss_tot <= 0:
        return 0.0
    return 1.0 - ss_res / ss_tot


# ──────────────────────────────────────────────────────────────────────


def _date_grouped_splits(
    dates: pd.Series, n_splits: int, embargo_days: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Walk-forward splits that respect date boundaries.

    Critical: rank-based targets at date D depend on ALL tickers at
    date D. If a row-based split puts some D-rows in train and others
    in test, the model can leak the rank distribution. We split by
    DATE instead — each fold's train/test partition is by date, and
    we skip an ``embargo_days`` gap between train and test so the
    forward-return target (which uses prices up to D + horizon) can't
    leak backward.
    """
    sorted_unique_dates = pd.to_datetime(dates).sort_values().unique()
    n_dates = len(sorted_unique_dates)
    if n_dates < n_splits + 2:
        return []
    # Expanding-window split: each fold uses earlier dates for train,
    # leaves embargo, then uses later block for test.
    fold_size = n_dates // (n_splits + 1)
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for k in range(1, n_splits + 1):
        train_end = fold_size * k
        test_start = train_end + embargo_days
        test_end = min(test_start + fold_size, n_dates)
        if test_start >= n_dates or test_end - test_start < 5:
            continue
        train_dates = set(sorted_unique_dates[:train_end])
        test_dates = set(sorted_unique_dates[test_start:test_end])
        train_idx = dates[dates.isin(train_dates)].index.to_numpy()
        test_idx = dates[dates.isin(test_dates)].index.to_numpy()
        if len(train_idx) > 0 and len(test_idx) > 0:
            splits.append((train_idx, test_idx))
    return splits


def train_one(
    df: pd.DataFrame,
    *,
    feature_cols: list[str],
    target_col: str,
    cluster_id: str,
    n_splits: int = 5,
    embargo_days: int = 21,
    params: Optional[dict] = None,
    num_boost_round: int = 2000,
    early_stopping_rounds: int = 100,
) -> Optional[TrainResultLGB]:
    """Train LightGBM with date-grouped time-series CV.

    df must include 'date' column for chronological ordering plus all
    feature_cols + target_col. Rows with NaN target are dropped.
    """
    if df.empty:
        return None
    df = df.dropna(subset=[target_col]).copy()
    df = df.dropna(subset=feature_cols, how="all")
    if len(df) < 200:
        return None

    df = df.sort_values("date").reset_index(drop=True)
    X = df[feature_cols].astype(float).values
    y = df[target_col].astype(float).values
    dates = df["date"]

    params = {**DEFAULT_LGB_PARAMS, **(params or {})}

    splits = _date_grouped_splits(dates, n_splits, embargo_days)
    if not splits:
        return None
    cv_metrics: list[FoldMetrics] = []
    oof_pred = np.full(len(df), np.nan)

    for fold_idx, (train_idx, test_idx) in enumerate(splits):
        if len(train_idx) < 100 or len(test_idx) < 20:
            continue
        X_tr, y_tr = X[train_idx], y[train_idx]
        X_te, y_te = X[test_idx], y[test_idx]

        train_set = lgb.Dataset(X_tr, label=y_tr, feature_name=feature_cols)
        valid_set = lgb.Dataset(X_te, label=y_te, feature_name=feature_cols, reference=train_set)
        booster = lgb.train(
            params, train_set,
            num_boost_round=num_boost_round,
            valid_sets=[valid_set],
            callbacks=[
                lgb.early_stopping(early_stopping_rounds, verbose=False),
                lgb.log_evaluation(0),
            ],
        )
        pred_tr = booster.predict(X_tr)
        pred_te = booster.predict(X_te)
        oof_pred[test_idx] = pred_te

        cv_metrics.append(FoldMetrics(
            fold=fold_idx,
            n_train=len(train_idx), n_test=len(test_idx),
            rmse_train=float(np.sqrt(np.mean((y_tr - pred_tr) ** 2))),
            rmse_test=float(np.sqrt(np.mean((y_te - pred_te) ** 2))),
            r2_train=_r2(y_tr, pred_tr),
            r2_test=_r2(y_te, pred_te),
            hit_rate_test=_hit_rate(y_te, pred_te),
            ic_spearman_test=_ic(y_te, pred_te),
        ))

    if not cv_metrics:
        return None

    mask = ~np.isnan(oof_pred)
    y_oof = y[mask]
    p_oof = oof_pred[mask]

    # Fit final model on ALL data for inference (post-CV)
    train_set = lgb.Dataset(X, label=y, feature_name=feature_cols)
    final_booster = lgb.train(
        params, train_set,
        num_boost_round=int(np.median([f.fold for f in cv_metrics]) + 200 or 500),
        callbacks=[lgb.log_evaluation(0)],
    )

    importance = dict(zip(
        feature_cols,
        final_booster.feature_importance(importance_type="gain").astype(float),
    ))

    return TrainResultLGB(
        target=target_col,
        cluster_id=cluster_id,
        booster=final_booster,
        feature_names=feature_cols,
        cv_metrics=cv_metrics,
        final_r2_oof=_r2(y_oof, p_oof),
        final_hit_rate_oof=_hit_rate(y_oof, p_oof),
        final_ic_oof=_ic(y_oof, p_oof),
        final_rmse_oof=float(np.sqrt(np.mean((y_oof - p_oof) ** 2))),
        n_samples=len(df),
        feature_importance=importance,
        notes=f"n_splits={n_splits} embargo={embargo_days}d",
    )


def train_per_cluster_and_global(
    df: pd.DataFrame,
    *,
    feature_cols: list[str],
    target_col: str,
    cluster_col: str = "cluster_id",
    min_samples_per_cluster: int = 500,
    **kwargs,
) -> dict[str, TrainResultLGB]:
    """Train one model per cluster_id + one global model.

    Returns {cluster_id_or_'__global__': result}.
    """
    results: dict[str, TrainResultLGB] = {}
    # Global model first (fallback)
    global_res = train_one(
        df, feature_cols=feature_cols, target_col=target_col,
        cluster_id="__global__", **kwargs,
    )
    if global_res is not None:
        results["__global__"] = global_res

    # Per-cluster
    for cid, group in df.groupby(cluster_col):
        if len(group) < min_samples_per_cluster:
            continue
        res = train_one(
            group, feature_cols=feature_cols, target_col=target_col,
            cluster_id=str(cid), **kwargs,
        )
        if res is not None:
            results[str(cid)] = res

    return results
