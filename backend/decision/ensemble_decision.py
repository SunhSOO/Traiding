"""Bridge between the new ML EnsembleSpec and the live decision engine.

The decision_runner consults this module to ask "what is the model's
prediction for (market, ticker, target) right now?" Returns:
- An ML prediction (or None if no active spec / cluster=ignore)
- A confidence proxy (0-1)
- The decision_gate from the spec ("trade" / "hold_only" / "ignore")

Production note: model boosters are heavy to load. We cache them per
process and only invalidate when ensembles.json mtime changes.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from training.ensemble import _predict_one
from training.ensemble_spec import EnsembleSpec, _registry_path, find_active, load_all
from training.features import build_feature_matrix
from training.model_registry import _booster_load, get_models_root, load_registry


@dataclass
class EnsemblePrediction:
    cluster_id: str
    target: str
    prediction: float           # raw model output (forward return space)
    confidence: float           # 0..1 derived from spec.holdout_ic_mean
    decision_gate: str          # "trade" | "hold_only" | "ignore"
    members_used: int


_CACHE_LOCK = threading.Lock()
_BOOSTER_CACHE: dict[tuple[str, str, str], tuple] = {}  # key → (booster, feature_names, model_kind)
_SPECS_MTIME: float = 0.0
_ACTIVE_SPECS: dict[tuple[str, str], EnsembleSpec] = {}


def _refresh_specs_if_needed() -> None:
    global _SPECS_MTIME, _ACTIVE_SPECS
    p = _registry_path()
    if not p.exists():
        _ACTIVE_SPECS = {}
        return
    mtime = p.stat().st_mtime
    if mtime == _SPECS_MTIME:
        return
    with _CACHE_LOCK:
        _SPECS_MTIME = mtime
        out: dict[tuple[str, str], EnsembleSpec] = {}
        for s in load_all():
            key = (s.cluster_id, s.target)
            # Keep the most-recent per (cluster, target)
            prev = out.get(key)
            if prev is None or s.validated_at > prev.validated_at:
                out[key] = s
        _ACTIVE_SPECS = out


def _load_spec_boosters(spec: EnsembleSpec):
    """Return list of (booster, model_kind, feature_names) for the spec.
    Cached across calls."""
    root = get_models_root()
    out = []
    for m in spec.members:
        cache_key = (m.run_id, m.model_kind, spec.cluster_id + "|" + spec.target)
        cached = _BOOSTER_CACHE.get(cache_key)
        if cached is None:
            entry = next(
                (e for e in load_registry()
                 if e["run_id"] == m.run_id and e["model_kind"] == m.model_kind
                 and e["cluster_id"] == spec.cluster_id and e["target"] == spec.target),
                None,
            )
            if entry is None:
                continue
            try:
                booster = _booster_load(m.model_kind, root / entry["model_path"])
            except Exception:
                continue
            cached = (booster, m.model_kind, entry["feature_names"])
            _BOOSTER_CACHE[cache_key] = cached
        out.append(cached)
    return out


def predict_ensemble(
    feature_row: pd.DataFrame,         # 1-row DataFrame for the (market, ticker, date)
    *, cluster_id: str, target: str,
) -> Optional[EnsemblePrediction]:
    """Top-level inference. Returns None when no active spec or feature
    columns are missing in caller's row."""
    _refresh_specs_if_needed()
    spec = _ACTIVE_SPECS.get((cluster_id, target))
    if spec is None:
        return None
    if spec.decision_gate == "ignore":
        return EnsemblePrediction(
            cluster_id=cluster_id, target=target, prediction=0.0,
            confidence=0.0, decision_gate="ignore", members_used=0,
        )

    boosters = _load_spec_boosters(spec)
    if not boosters or len(boosters) != len(spec.weights):
        return None

    combined = 0.0
    used = 0
    for (booster, kind, feats), w in zip(boosters, spec.weights):
        missing = [c for c in feats if c not in feature_row.columns]
        if missing:
            continue
        X = feature_row[feats].astype(float).values
        try:
            pred = _predict_one(kind, booster, X, feats)
        except Exception:
            continue
        combined += float(pred[0]) * w
        used += 1

    if used == 0:
        return None

    # Confidence proxy: capped IC scaled to [0, 1]
    conf = max(0.0, min(1.0, spec.holdout_ic_mean * 5.0))  # IC 0.20 → conf 1.0
    return EnsemblePrediction(
        cluster_id=cluster_id, target=target, prediction=combined,
        confidence=conf, decision_gate=spec.decision_gate, members_used=used,
    )
