"""Ensemble inference — combine multiple saved models per cluster.

Workflow:
1. From the registry, find all artifacts for (target, cluster_id).
2. Load each model.
3. Predict on the same feature matrix.
4. Combine by IC-weighted average (models with higher OOF IC get more weight).

We do NOT retrain ensembles — the per-model OOF metrics are already
out-of-sample and trustworthy, so the IC-weighted combination is a
zero-cost improvement over the best single model.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from training.model_registry import get_models_root, load_registry, _booster_load


@dataclass
class LoadedModel:
    entry: dict          # registry row
    booster: object


def load_top_models(
    *, target: str, cluster_id: str,
    top_k: int = 3,
    metric: str = "final_ic_oof",
    min_metric: float = 0.0,
) -> list[LoadedModel]:
    """Return the top-K models for the (target, cluster_id) keyed on `metric`."""
    entries = [
        e for e in load_registry()
        if e["target"] == target and e["cluster_id"] == cluster_id
        and e.get(metric, 0.0) >= min_metric
    ]
    if not entries:
        return []
    entries.sort(key=lambda e: e.get(metric, 0.0), reverse=True)
    selected = entries[:top_k]
    root = get_models_root()
    out: list[LoadedModel] = []
    for e in selected:
        try:
            booster = _booster_load(e["model_kind"], root / e["model_path"])
            out.append(LoadedModel(entry=e, booster=booster))
        except Exception:
            continue
    return out


def _predict_one(model_kind: str, booster, X: np.ndarray, feature_names: list[str]) -> np.ndarray:
    if model_kind == "lgbm":
        return booster.predict(X)
    if model_kind == "xgb":
        import xgboost as xgb
        d = xgb.DMatrix(X, feature_names=feature_names)
        return booster.predict(d)
    if model_kind == "catboost":
        return booster.predict(X)
    if model_kind == "ridge":
        # Caller must provide pre-imputed X (we don't carry the imputer here).
        return booster.predict(X)
    raise ValueError(f"unknown model_kind={model_kind}")


def predict_ensemble(
    X: np.ndarray, *,
    target: str, cluster_id: str,
    top_k: int = 3,
    weighting: str = "ic",
    metric: str = "final_ic_oof",
) -> Optional[np.ndarray]:
    """Predict via IC-weighted ensemble. Returns shape (n_samples,)."""
    models = load_top_models(target=target, cluster_id=cluster_id,
                              top_k=top_k, metric=metric)
    if not models:
        return None

    preds = []
    weights = []
    for m in models:
        try:
            p = _predict_one(m.entry["model_kind"], m.booster, X,
                             m.entry["feature_names"])
            preds.append(p)
            if weighting == "ic":
                w = max(m.entry.get(metric, 0.0), 0.0)
            elif weighting == "inverse_rmse":
                rmse = m.entry.get("final_rmse_oof", 1.0)
                w = 1.0 / max(rmse, 1e-6)
            else:
                w = 1.0
            weights.append(w)
        except Exception:
            continue

    if not preds:
        return None
    P = np.stack(preds)
    w = np.array(weights, dtype="float64")
    if w.sum() <= 0:
        w = np.ones_like(w)
    w = w / w.sum()
    return (P * w[:, None]).sum(axis=0)


def ensemble_metrics(
    y_true: np.ndarray, y_pred: np.ndarray,
) -> dict:
    """Compute the standard 3-metric set on ensemble predictions."""
    if len(y_true) < 2:
        return {"r2": 0.0, "hit": 0.0, "ic": 0.0, "rmse": 0.0}
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    r2 = 0.0 if ss_tot <= 0 else 1.0 - ss_res / ss_tot
    hit = float(((y_true >= 0) == (y_pred >= 0)).mean())
    rt = pd.Series(y_true).rank().values
    rp = pd.Series(y_pred).rank().values
    if np.std(rt) == 0 or np.std(rp) == 0:
        ic = 0.0
    else:
        ic = float(np.corrcoef(rt, rp)[0, 1])
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    return {"r2": r2, "hit": hit, "ic": ic, "rmse": rmse}
