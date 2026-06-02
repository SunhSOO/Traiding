"""Optuna hyperparameter search for LGBM/XGB/CatBoost.

For each model_kind we define a search space, then run N trials with
the date-grouped TS CV objective (mean OOF IC across folds, since IC
is the most stable metric for noisy financial targets).

Each trial trains via ``training.multi_trainer.train_model`` so the
CV protocol matches the comparison script — apples-to-apples.

The best trial's model is saved to the registry; runner-up models can
be kept too (we save *all* trials? overkill — keep only top-K to save
disk).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import optuna
import pandas as pd

from training.multi_trainer import train_model


optuna.logging.set_verbosity(optuna.logging.WARNING)


def _params_lgbm(trial: optuna.Trial) -> dict:
    return {
        "objective": "regression",
        "metric": "rmse",
        "boosting_type": "gbdt",
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 15, 127),
        "max_depth": trial.suggest_int("max_depth", 4, 10),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 20, 200),
        "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
        "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
        "bagging_freq": trial.suggest_int("bagging_freq", 1, 10),
        "lambda_l1": trial.suggest_float("lambda_l1", 0.0, 2.0),
        "lambda_l2": trial.suggest_float("lambda_l2", 0.0, 5.0),
        "verbose": -1,
    }


def _params_xgb(trial: optuna.Trial) -> dict:
    return {
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
        "max_depth": trial.suggest_int("max_depth", 4, 10),
        "min_child_weight": trial.suggest_int("min_child_weight", 10, 200),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.0, 5.0),
        "tree_method": "hist",
        "verbosity": 0,
    }


def _params_catboost(trial: optuna.Trial) -> dict:
    return {
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
        "max_depth": trial.suggest_int("max_depth", 4, 10),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1.0, 10.0),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bylevel": trial.suggest_float("colsample_bylevel", 0.5, 1.0),
        "verbose": 0,
    }


_SPACES = {
    "lgbm": _params_lgbm,
    "xgb": _params_xgb,
    "catboost": _params_catboost,
}


@dataclass
class TuneResult:
    model_kind: str
    cluster_id: str
    target: str
    best_params: dict
    best_value: float                  # mean IC OOF across folds
    best_metrics: dict                 # full TrainResult-derived metrics
    booster: object                    # the best-trial fitted model
    feature_names: list[str]
    n_trials: int


def tune(
    df: pd.DataFrame,
    *,
    model_kind: str,
    feature_cols: list[str],
    target_col: str,
    cluster_id: str,
    n_trials: int = 50,
    n_splits: int = 4,
    embargo_days: int = 21,
    timeout_s: Optional[int] = None,
) -> Optional[TuneResult]:
    if model_kind not in _SPACES:
        raise ValueError(f"no Optuna space for model_kind={model_kind}")
    sampler = optuna.samplers.TPESampler(seed=42, multivariate=True)
    study = optuna.create_study(direction="maximize", sampler=sampler)

    best_holder: dict = {"result": None, "booster": None}

    def objective(trial: optuna.Trial) -> float:
        params = _SPACES[model_kind](trial)
        result = train_model(
            df, model_kind=model_kind, feature_cols=feature_cols,
            target_col=target_col, cluster_id=cluster_id,
            n_splits=n_splits, embargo_days=embargo_days,
            params=params,
        )
        if result is None:
            return -1.0
        ic = result.final_ic_oof
        # Track best across trials manually so we keep the booster.
        if best_holder["result"] is None or ic > best_holder["result"].final_ic_oof:
            best_holder["result"] = result
            best_holder["booster"] = result.booster
        return ic

    study.optimize(objective, n_trials=n_trials, timeout=timeout_s, show_progress_bar=False)

    best = best_holder["result"]
    if best is None:
        return None

    return TuneResult(
        model_kind=model_kind,
        cluster_id=cluster_id,
        target=target_col,
        best_params=study.best_params,
        best_value=study.best_value,
        best_metrics=best.as_metrics_jsonb(),
        booster=best.booster,
        feature_names=feature_cols,
        n_trials=n_trials,
    )
