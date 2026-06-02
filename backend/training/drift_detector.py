"""Drift detection — should we trigger a retrain?

Two complementary signals:

1. **Output drift (KS-test)**: compare the distribution of recent
   predictions (last N days) vs the distribution observed during the
   training window. If KS p-value < threshold the model's outputs are
   shifting — possible regime change.

2. **Holdout IC degradation**: compute IC on the last 21 trading days
   *using the deployed ensemble spec*; compare to the spec's
   `holdout_ic_mean`. If rolling IC drops below 50% of the validated
   level for K consecutive checks → drift signal.

Either signal flips the retrain flag for that (cluster, target).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as _stats

from training.ensemble_spec import EnsembleSpec, find_active
from training.ensemble import _predict_one
from training.model_registry import _booster_load, get_models_root, load_registry


@dataclass
class DriftSignal:
    cluster_id: str
    target: str
    ks_pvalue: Optional[float] = None
    ks_statistic: Optional[float] = None
    rolling_ic: Optional[float] = None
    validated_ic: Optional[float] = None
    rolling_ic_drop_ratio: Optional[float] = None
    drift_triggered: bool = False
    reasons: list[str] = field(default_factory=list)
    checked_at: str = ""


def ks_test_distribution(
    recent_preds: np.ndarray,
    reference_preds: np.ndarray,
    p_threshold: float = 0.01,
) -> tuple[float, float, bool]:
    """Two-sample Kolmogorov-Smirnov. Returns (D, p, drift_triggered)."""
    if len(recent_preds) < 30 or len(reference_preds) < 30:
        return 0.0, 1.0, False
    res = _stats.ks_2samp(recent_preds, reference_preds)
    return float(res.statistic), float(res.pvalue), bool(res.pvalue < p_threshold)


def _ic(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 2:
        return 0.0
    rt = pd.Series(y_true).rank().values
    rp = pd.Series(y_pred).rank().values
    if np.std(rt) == 0 or np.std(rp) == 0:
        return 0.0
    return float(np.corrcoef(rt, rp)[0, 1])


def check_spec_drift(
    spec: EnsembleSpec,
    recent_df: pd.DataFrame,        # last 21 days of features + target
    reference_df: pd.DataFrame,     # training-period features
    *,
    ic_drop_threshold: float = 0.5,
    ks_threshold: float = 0.01,
) -> DriftSignal:
    """Score the spec's ensemble on both windows and look for drift."""
    sig = DriftSignal(
        cluster_id=spec.cluster_id, target=spec.target,
        validated_ic=spec.holdout_ic_mean,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )
    root = get_models_root()
    # Materialise the spec's members
    boosters = []
    for m in spec.members:
        # Look up registry to find model_path + feature_names
        registry_match = next(
            (e for e in load_registry()
             if e["run_id"] == m.run_id and e["model_kind"] == m.model_kind
             and e["cluster_id"] == spec.cluster_id and e["target"] == spec.target),
            None,
        )
        if registry_match is None:
            sig.reasons.append(f"missing registry entry for {m.run_id}/{m.model_kind}")
            continue
        try:
            b = _booster_load(m.model_kind, root / registry_match["model_path"])
            boosters.append((b, m.model_kind, registry_match["feature_names"]))
        except Exception as e:
            sig.reasons.append(f"load fail {m.run_id}/{m.model_kind}: {e}")

    if not boosters or len(boosters) != len(spec.weights):
        sig.drift_triggered = True
        sig.reasons.append("could not load all spec members")
        return sig

    def _pred(df: pd.DataFrame) -> Optional[np.ndarray]:
        preds = []
        for (b, kind, feats), w in zip(boosters, spec.weights):
            missing = [f for f in feats if f not in df.columns]
            if missing:
                return None
            X = df[feats].astype(float).values
            try:
                p = _predict_one(kind, b, X, feats)
            except Exception:
                return None
            preds.append(p * w)
        return sum(preds)

    rec_pred = _pred(recent_df)
    ref_pred = _pred(reference_df)
    if rec_pred is None or ref_pred is None:
        sig.drift_triggered = True
        sig.reasons.append("feature columns missing in window")
        return sig

    # 1. KS test on predictions
    ks_d, ks_p, ks_drift = ks_test_distribution(rec_pred, ref_pred, ks_threshold)
    sig.ks_statistic = ks_d
    sig.ks_pvalue = ks_p
    if ks_drift:
        sig.reasons.append(f"output KS-test p={ks_p:.4f} < {ks_threshold}")
        sig.drift_triggered = True

    # 2. Rolling IC vs validated
    y_recent = recent_df.dropna(subset=[spec.target])[spec.target].astype(float).values
    rec_pred_aligned = rec_pred[: len(y_recent)]
    rolling_ic = _ic(y_recent, rec_pred_aligned)
    sig.rolling_ic = rolling_ic
    sig.rolling_ic_drop_ratio = (
        rolling_ic / spec.holdout_ic_mean if spec.holdout_ic_mean else 0.0
    )
    if spec.holdout_ic_mean > 0 and rolling_ic < spec.holdout_ic_mean * ic_drop_threshold:
        sig.reasons.append(
            f"rolling IC {rolling_ic:.4f} below {ic_drop_threshold:.0%} "
            f"of validated {spec.holdout_ic_mean:.4f}"
        )
        sig.drift_triggered = True

    return sig
