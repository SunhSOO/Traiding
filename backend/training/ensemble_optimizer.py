"""Forward-selection ensemble optimizer.

For each (cluster, target) we evaluate all registered models on
multi-window holdout (last 21d / 42d / 63d), then greedily add
models that improve the mean OOS IC.

Algorithm:
1. Score every registered model on each holdout window → mean IC
2. If max single-model IC < `min_ic`: `decision_gate=ignore`, return empty
3. Pick best single → ensemble of size 1
4. For each candidate addition:
   - Try weights from {0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9}
   - Compute combined holdout IC across windows
   - Accept if improvement > `min_improvement` AND
     improvement > 0.5 × IC std across windows (statistical significance)
5. Repeat until no improvement or max_members reached
6. Persist final spec to ensembles.json

Compute budget: ~200 evals per (cluster, target). At <1s each, runs
under 5 min per pair; ~1.5h for full universe (22 × 3 horizons).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from training.ensemble import _predict_one
from training.ensemble_spec import (
    EnsembleSpec, ModelRef, append_spec,
)
from training.model_registry import _booster_load, get_models_root, load_registry


def _ic(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 2:
        return 0.0
    rt = pd.Series(y_true).rank().values
    rp = pd.Series(y_pred).rank().values
    if np.std(rt) == 0 or np.std(rp) == 0:
        return 0.0
    return float(np.corrcoef(rt, rp)[0, 1])


def _hit(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) == 0:
        return 0.0
    return float(((y_true >= 0) == (y_pred >= 0)).mean())


@dataclass
class CandidatePred:
    run_id: str
    model_kind: str
    feature_names: list[str]
    booster: object


def _load_candidates(target: str, cluster_id: str) -> list[CandidatePred]:
    """Pull every registered model for (target, cluster) and hydrate."""
    root = get_models_root()
    out: list[CandidatePred] = []
    for e in load_registry():
        if e["target"] != target or e["cluster_id"] != cluster_id:
            continue
        try:
            booster = _booster_load(e["model_kind"], root / e["model_path"])
        except Exception:
            continue
        out.append(CandidatePred(
            run_id=e["run_id"], model_kind=e["model_kind"],
            feature_names=list(e["feature_names"]), booster=booster,
        ))
    return out


def _predict_safe(c: CandidatePred, df: pd.DataFrame) -> Optional[np.ndarray]:
    """Predict using ONLY c.feature_names (may be subset of df columns)."""
    missing = [col for col in c.feature_names if col not in df.columns]
    if missing:
        return None
    X = df[c.feature_names].astype(float).values
    try:
        return _predict_one(c.model_kind, c.booster, X, c.feature_names)
    except Exception:
        return None


def _holdout_windows(feat_df: pd.DataFrame, target: str,
                     day_windows: tuple[int, ...] = (21, 42, 63)) -> list[pd.DataFrame]:
    """Return last-N-day slices (each disjoint from the *prior* longer one
    if smaller; we use OVERLAPPING windows — last 21 is inside last 42).

    Overlapping is fine here because we're averaging IC across windows,
    not summing risk.
    """
    df = feat_df.dropna(subset=[target]).copy()
    df["date"] = pd.to_datetime(df["date"])
    end = df["date"].max()
    out = []
    for d in day_windows:
        start = end - pd.Timedelta(days=d)
        sub = df[df["date"] > start]
        if len(sub) >= 50:
            out.append(sub)
    return out


@dataclass
class OptimizeReport:
    cluster_id: str
    target: str
    candidates_considered: int
    n_holdout_windows: int
    final_members: int
    final_ic_mean: float
    final_ic_std: float
    chosen_gate: str
    notes: str = ""


def optimize(
    feat_df: pd.DataFrame,
    *,
    cluster_id: str,
    target: str,
    day_windows: tuple[int, ...] = (21, 42, 63),
    max_members: int = 4,
    min_single_ic: float = 0.03,
    min_improvement: float = 0.005,
    weight_grid: tuple[float, ...] = (0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9),
    persist: bool = True,
) -> tuple[Optional[EnsembleSpec], OptimizeReport]:
    """Find the best ensemble for (cluster_id, target). Persists to
    ensembles.json when ``persist=True`` (default)."""
    candidates = _load_candidates(target, cluster_id)
    holdouts = _holdout_windows(feat_df, target, day_windows)
    if not holdouts:
        return None, OptimizeReport(
            cluster_id=cluster_id, target=target,
            candidates_considered=len(candidates),
            n_holdout_windows=0, final_members=0,
            final_ic_mean=0.0, final_ic_std=0.0,
            chosen_gate="ignore", notes="no holdout windows",
        )

    # 1. Score every candidate on every window. Cache predictions.
    pred_cache: dict[int, list[np.ndarray]] = {}  # idx → [pred_window_0, ...]
    target_cache: list[np.ndarray] = [h[target].astype(float).values for h in holdouts]
    ic_single: list[float] = [0.0] * len(candidates)
    for i, c in enumerate(candidates):
        per_window = []
        ics_w = []
        ok = True
        for h, y in zip(holdouts, target_cache):
            p = _predict_safe(c, h)
            if p is None or len(p) != len(y):
                ok = False
                break
            per_window.append(p)
            ics_w.append(_ic(y, p))
        if ok:
            pred_cache[i] = per_window
            ic_single[i] = float(np.mean(ics_w))

    valid_idx = [i for i, c in enumerate(candidates) if i in pred_cache]
    if not valid_idx:
        return None, OptimizeReport(
            cluster_id=cluster_id, target=target,
            candidates_considered=len(candidates),
            n_holdout_windows=len(holdouts), final_members=0,
            final_ic_mean=0.0, final_ic_std=0.0,
            chosen_gate="ignore", notes="no models could predict",
        )

    # 2. Decision gate — best single below threshold → ignore cluster
    best_single_i = max(valid_idx, key=lambda i: ic_single[i])
    best_single_ic = ic_single[best_single_i]
    if best_single_ic < min_single_ic:
        return None, OptimizeReport(
            cluster_id=cluster_id, target=target,
            candidates_considered=len(candidates),
            n_holdout_windows=len(holdouts), final_members=0,
            final_ic_mean=best_single_ic, final_ic_std=0.0,
            chosen_gate="ignore",
            notes=f"best single IC {best_single_ic:.4f} < {min_single_ic}",
        )

    # 3. Forward selection
    chosen: list[int] = [best_single_i]
    chosen_weights: list[float] = [1.0]
    cur_preds = pred_cache[best_single_i]  # list per window
    cur_ic_per_window = [_ic(y, p) for y, p in zip(target_cache, cur_preds)]
    cur_ic_mean = float(np.mean(cur_ic_per_window))
    cur_ic_std = float(np.std(cur_ic_per_window))

    while len(chosen) < max_members:
        best_gain = 0.0
        best_addition: tuple[int, float] | None = None  # (idx, weight)
        best_new_preds: list[np.ndarray] | None = None
        best_new_ic_window: list[float] | None = None

        for j in valid_idx:
            if j in chosen:
                continue
            cand_preds = pred_cache[j]
            for w_new in weight_grid:
                w_old = 1.0 - w_new
                test_preds = [w_old * cp + w_new * np_ for cp, np_ in zip(cur_preds, cand_preds)]
                test_ic_window = [_ic(y, p) for y, p in zip(target_cache, test_preds)]
                test_ic_mean = float(np.mean(test_ic_window))
                gain = test_ic_mean - cur_ic_mean
                # Require gain > min_improvement AND > 0.5 × std (significance)
                test_ic_std = float(np.std(test_ic_window))
                threshold = max(min_improvement, 0.5 * max(test_ic_std, cur_ic_std))
                if gain > best_gain and gain > threshold:
                    best_gain = gain
                    best_addition = (j, w_new)
                    best_new_preds = test_preds
                    best_new_ic_window = test_ic_window

        if best_addition is None:
            break
        j, w_new = best_addition
        # Rescale existing weights
        w_old = 1.0 - w_new
        chosen_weights = [w * w_old for w in chosen_weights]
        chosen.append(j)
        chosen_weights.append(w_new)
        cur_preds = best_new_preds
        cur_ic_per_window = best_new_ic_window
        cur_ic_mean = float(np.mean(cur_ic_per_window))
        cur_ic_std = float(np.std(cur_ic_per_window))

    members = [
        ModelRef(run_id=candidates[i].run_id, model_kind=candidates[i].model_kind)
        for i in chosen
    ]
    holdout_window_labels = [f"last_{d}d" for d in day_windows]
    spec = EnsembleSpec(
        cluster_id=cluster_id, target=target,
        members=members, weights=[round(w, 4) for w in chosen_weights],
        holdout_ic_mean=round(cur_ic_mean, 5),
        holdout_ic_std=round(cur_ic_std, 5),
        holdout_hit_mean=round(float(np.mean([
            _hit(y, p) for y, p in zip(target_cache, cur_preds)
        ])), 5),
        holdout_windows=holdout_window_labels[:len(holdouts)],
        decision_gate="trade",
        validated_at=datetime.now(timezone.utc).isoformat(),
    )
    if persist:
        append_spec(spec)

    return spec, OptimizeReport(
        cluster_id=cluster_id, target=target,
        candidates_considered=len(candidates),
        n_holdout_windows=len(holdouts),
        final_members=len(chosen),
        final_ic_mean=cur_ic_mean,
        final_ic_std=cur_ic_std,
        chosen_gate="trade",
    )
