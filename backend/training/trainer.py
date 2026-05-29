"""Per-cluster OLS regression via numpy.linalg.lstsq.

Fits the linear model::

    forward_return ≈ intercept + w_F * F_score + w_T * T_score + w_I * I_score

Returns normalised weights such that ``w_F + w_T + w_I == 1`` (matching
the convention :class:`decision.types.DecisionConfig` already uses).
The raw OLS coefficients can be negative — we treat each coefficient's
SIGN as its directional contribution and take ``max(0, coef)`` before
normalising, so that a module the regression "doesn't trust" effectively
drops out of the composite for that cluster. Sign and intercept are
preserved in the result for audit.

Sample weighting (optional): if ``use_confidence_weights`` is True,
each sample is weighted by ``mean(F_conf, T_conf, I_conf)`` so that
low-confidence inputs don't drag the fit.

Hit rate: fraction of test samples where ``sign(predicted) == sign(actual)``
— a more meaningful score for trading than R² when targets are noisy.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np

from core.logging import get_logger
from training.types import ClusterMetrics, TrainResult, TrainSample, WalkForwardSplit

log = get_logger(__name__)


def fit_cluster(
    samples: Iterable[TrainSample],
    *,
    cluster_id: str,
    model_version: str,
    use_confidence_weights: bool = True,
    walk_forward_splits: Optional[list[WalkForwardSplit]] = None,
) -> Optional[TrainResult]:
    """Fit one cluster. Returns ``None`` when too few samples to be
    meaningful (< 30)."""
    samples_list = list(samples)
    if len(samples_list) < 30:
        log.warning("trainer.too_few_samples", cluster=cluster_id, n=len(samples_list))
        return None

    X, y, w = _design_matrix(samples_list, use_confidence_weights=use_confidence_weights)

    coefs, intercept, r2 = _weighted_lstsq(X, y, w)
    # coefs is (3,) for [F, T, I]
    w_f, w_t, w_i = float(coefs[0]), float(coefs[1]), float(coefs[2])
    norm = _normalise_weights(w_f, w_t, w_i)

    # Hit rate over the full sample (in-sample) — cheap sanity proxy.
    predicted = X @ coefs + intercept
    hit_rate_in = _hit_rate(predicted, y)

    # Optional walk-forward evaluation
    r2_wf: Optional[float] = None
    if walk_forward_splits:
        r2_wf = _walk_forward_r2(samples_list, walk_forward_splits, use_confidence_weights)

    n_tickers = len({s.ticker for s in samples_list})
    metrics = ClusterMetrics(
        n_samples=len(samples_list),
        n_tickers=n_tickers,
        r2_in_sample=r2,
        r2_walk_forward=r2_wf,
        hit_rate=hit_rate_in,
        notes=(
            f"OLS coefs raw: F={w_f:.4f}, T={w_t:.4f}, I={w_i:.4f}; "
            f"normalised to non-negative+sum=1."
        ),
    )

    return TrainResult(
        cluster_id=cluster_id,
        w_fundamental=norm["F"],
        w_technical=norm["T"],
        w_information=norm["I"],
        intercept=float(intercept),
        metrics=metrics,
        model_version=model_version,
    )


# ──────────────────────────────────────────────────────────────────────
# Math
# ──────────────────────────────────────────────────────────────────────


def _design_matrix(
    samples: list[TrainSample], *, use_confidence_weights: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X = np.array([[s.f_score, s.t_score, s.i_score] for s in samples], dtype=np.float64)
    y = np.array([s.target for s in samples], dtype=np.float64)
    if use_confidence_weights:
        w = np.array([
            (s.f_confidence + s.t_confidence + s.i_confidence) / 3.0
            for s in samples
        ], dtype=np.float64)
        # Guard against all-zero weights
        if not np.any(w > 0):
            w = np.ones_like(y)
    else:
        w = np.ones_like(y)
    return X, y, w


def _weighted_lstsq(
    X: np.ndarray, y: np.ndarray, w: np.ndarray,
) -> tuple[np.ndarray, float, float]:
    """Solve weighted OLS for [F, T, I] coefs + intercept. Returns
    (coefs[3], intercept, r2)."""
    # Add an intercept column
    X_aug = np.hstack([X, np.ones((X.shape[0], 1))])
    # Weight rows by sqrt(w) so unweighted lstsq on weighted system
    # solves the weighted problem.
    sw = np.sqrt(np.maximum(w, 0))
    X_w = X_aug * sw[:, None]
    y_w = y * sw

    coefs_aug, *_ = np.linalg.lstsq(X_w, y_w, rcond=None)
    coefs = coefs_aug[:3]
    intercept = coefs_aug[3]

    # R² on the WEIGHTED system
    y_pred = X_aug @ coefs_aug
    ss_res = float(np.sum(w * (y - y_pred) ** 2))
    y_mean = float(np.sum(w * y) / max(np.sum(w), 1e-12))
    ss_tot = float(np.sum(w * (y - y_mean) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return coefs, float(intercept), r2


def _walk_forward_r2(
    samples: list[TrainSample],
    splits: list[WalkForwardSplit],
    use_confidence_weights: bool,
) -> Optional[float]:
    """Average R² across walk-forward folds."""
    r2s: list[float] = []
    for sp in splits:
        train = [s for s in samples if sp.train_start <= s.score_ts < sp.train_end]
        test = [s for s in samples if sp.test_start <= s.score_ts < sp.test_end]
        if len(train) < 20 or len(test) < 5:
            continue
        Xtr, ytr, wtr = _design_matrix(train, use_confidence_weights=use_confidence_weights)
        Xte, yte, _ = _design_matrix(test, use_confidence_weights=False)
        coefs, intercept, _ = _weighted_lstsq(Xtr, ytr, wtr)
        y_pred = Xte @ coefs + intercept
        ss_res = float(np.sum((yte - y_pred) ** 2))
        y_mean = float(np.mean(yte))
        ss_tot = float(np.sum((yte - y_mean) ** 2))
        if ss_tot > 1e-12:
            r2s.append(1 - ss_res / ss_tot)
    if not r2s:
        return None
    return float(np.mean(r2s))


def _hit_rate(predicted: np.ndarray, actual: np.ndarray) -> float:
    """Sign-agreement rate. Returns 0..1."""
    if predicted.size == 0:
        return 0.0
    agree = (np.sign(predicted) == np.sign(actual))
    return float(np.mean(agree))


def _normalise_weights(w_f: float, w_t: float, w_i: float) -> dict[str, float]:
    """Clip negatives to 0; renormalise to sum 1. If all clipped, fall
    back to equal thirds (the trainer found no signal)."""
    raw = {"F": max(0.0, w_f), "T": max(0.0, w_t), "I": max(0.0, w_i)}
    total = sum(raw.values())
    if total <= 0 or not all(math.isfinite(v) for v in raw.values()):
        return {"F": 1 / 3, "T": 1 / 3, "I": 1 / 3}
    return {k: v / total for k, v in raw.items()}
