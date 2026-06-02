"""Unified trainer for multiple model families.

All trainers share the same date-grouped time-series CV + embargo, the
same target/feature split, and the same metrics — so cross-model
comparison is apples-to-apples.

Supported model_kind:
  - "lgbm"     LightGBM (default; what production currently uses)
  - "xgb"      XGBoost
  - "catboost" CatBoost
  - "ridge"    Ridge regression (sklearn) — baseline

LSTM lives in lstm_trainer.py because its data shape (sequences) is
incompatible with the cross-sectional rows here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from training.lgbm_trainer import (
    DEFAULT_LGB_PARAMS,
    FoldMetrics,
    TrainResultLGB as TrainResult,
    _date_grouped_splits,
    _hit_rate,
    _ic,
    _r2,
)


def _fit_lgbm(X_tr, y_tr, X_te, y_te, feature_cols, params, n_boost, early_stop):
    import lightgbm as lgb
    train_set = lgb.Dataset(X_tr, label=y_tr, feature_name=feature_cols)
    valid_set = lgb.Dataset(X_te, label=y_te, feature_name=feature_cols, reference=train_set)
    booster = lgb.train(
        params, train_set,
        num_boost_round=n_boost,
        valid_sets=[valid_set],
        callbacks=[
            lgb.early_stopping(early_stop, verbose=False),
            lgb.log_evaluation(0),
        ],
    )
    return booster, lambda b, X: b.predict(X), booster.feature_importance(importance_type="gain").astype(float)


def _fit_xgb(X_tr, y_tr, X_te, y_te, feature_cols, params, n_boost, early_stop):
    import xgboost as xgb
    dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=feature_cols)
    dval = xgb.DMatrix(X_te, label=y_te, feature_names=feature_cols)
    booster = xgb.train(
        params, dtrain,
        num_boost_round=n_boost,
        evals=[(dval, "val")],
        early_stopping_rounds=early_stop,
        verbose_eval=0,
    )
    def predict_fn(b, X):
        d = xgb.DMatrix(X, feature_names=feature_cols)
        return b.predict(d)
    score = booster.get_score(importance_type="gain")
    importance = np.array([score.get(f, 0.0) for f in feature_cols])
    return booster, predict_fn, importance


def _fit_catboost(X_tr, y_tr, X_te, y_te, feature_cols, params, n_boost, early_stop):
    from catboost import CatBoostRegressor
    model = CatBoostRegressor(
        iterations=n_boost,
        learning_rate=params.get("learning_rate", 0.05),
        depth=params.get("max_depth", 6),
        l2_leaf_reg=params.get("l2_leaf_reg", 3),
        early_stopping_rounds=early_stop,
        verbose=0,
        random_seed=42,
    )
    model.fit(X_tr, y_tr, eval_set=(X_te, y_te))
    return model, lambda m, X: m.predict(X), model.feature_importances_


def _fit_ridge(X_tr, y_tr, X_te, y_te, feature_cols, params, n_boost, early_stop):
    from sklearn.linear_model import Ridge
    from sklearn.impute import SimpleImputer
    imp = SimpleImputer(strategy="median")
    X_tr_imp = imp.fit_transform(X_tr)
    model = Ridge(alpha=params.get("alpha", 1.0))
    model.fit(X_tr_imp, y_tr)
    def predict_fn(m, X):
        X_imp = imp.transform(X)
        return m.predict(X_imp)
    return model, predict_fn, np.abs(model.coef_)


_FITTERS = {
    "lgbm": _fit_lgbm,
    "xgb": _fit_xgb,
    "catboost": _fit_catboost,
    "ridge": _fit_ridge,
}


_DEFAULT_PARAMS = {
    "lgbm": DEFAULT_LGB_PARAMS,
    "xgb": {
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "learning_rate": 0.03,
        "max_depth": 6,
        "min_child_weight": 50,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 0.5,
        "verbosity": 0,
        "tree_method": "hist",
    },
    "catboost": {
        "learning_rate": 0.03,
        "max_depth": 6,
        "l2_leaf_reg": 5,
    },
    "ridge": {"alpha": 1.0},
}


def train_model(
    df: pd.DataFrame,
    *,
    model_kind: str,
    feature_cols: list[str],
    target_col: str,
    cluster_id: str,
    n_splits: int = 5,
    embargo_days: int = 21,
    params: Optional[dict] = None,
    num_boost_round: int = 2000,
    early_stopping_rounds: int = 100,
) -> Optional[TrainResult]:
    """Train ``model_kind`` on date-grouped TS CV. Returns TrainResult.

    NaN imputation: trees handle NaN natively; ridge uses median imputer.
    """
    if df.empty:
        return None
    df = df.dropna(subset=[target_col]).copy()
    df = df.dropna(subset=feature_cols, how="all")
    if len(df) < 200:
        return None

    df = df.sort_values("date").reset_index(drop=True)

    # Trees handle NaN; for ridge we pre-impute. Trees still benefit from
    # filling extreme sentinels — we leave NaN as-is and let LightGBM/XGB/CatBoost
    # learn their own missing-value direction.
    X = df[feature_cols].astype(float).values
    y = df[target_col].astype(float).values
    dates = df["date"]

    fitter = _FITTERS[model_kind]
    params = {**_DEFAULT_PARAMS[model_kind], **(params or {})}

    splits = _date_grouped_splits(dates, n_splits, embargo_days)
    if not splits:
        return None

    cv_metrics: list[FoldMetrics] = []
    oof_pred = np.full(len(df), np.nan)
    booster = None
    predict_fn = None
    importance = np.zeros(len(feature_cols))

    for fold_idx, (train_idx, test_idx) in enumerate(splits):
        if len(train_idx) < 100 or len(test_idx) < 20:
            continue
        X_tr, y_tr = X[train_idx], y[train_idx]
        X_te, y_te = X[test_idx], y[test_idx]
        try:
            booster, predict_fn, importance_fold = fitter(
                X_tr, y_tr, X_te, y_te, feature_cols, params,
                num_boost_round, early_stopping_rounds,
            )
        except Exception as e:
            cv_metrics.append(FoldMetrics(
                fold=fold_idx, n_train=len(train_idx), n_test=len(test_idx),
                rmse_train=float("nan"), rmse_test=float("nan"),
                r2_train=0.0, r2_test=0.0, hit_rate_test=0.0,
                ic_spearman_test=0.0,
            ))
            continue
        pred_tr = predict_fn(booster, X_tr)
        pred_te = predict_fn(booster, X_te)
        oof_pred[test_idx] = pred_te
        importance = importance + importance_fold / len(splits)
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

    return TrainResult(
        target=target_col,
        cluster_id=cluster_id,
        booster=booster,
        feature_names=feature_cols,
        cv_metrics=cv_metrics,
        final_r2_oof=_r2(y_oof, p_oof),
        final_hit_rate_oof=_hit_rate(y_oof, p_oof),
        final_ic_oof=_ic(y_oof, p_oof),
        final_rmse_oof=float(np.sqrt(np.mean((y_oof - p_oof) ** 2))),
        n_samples=len(df),
        feature_importance=dict(zip(feature_cols, importance.tolist())),
        notes=f"model={model_kind} n_splits={n_splits} embargo={embargo_days}d",
    )
