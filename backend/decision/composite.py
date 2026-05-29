"""Composite scoring — turn three ModuleScores into one composite.

Default rule: weighted average of (F, T, I) scores using the operator-
configured weights, re-normalised to ignore modules that didn't run.

Composite confidence = weighted average of per-module confidences
times the "coverage ratio" (how many of the three modules
contributed). A ticker with only F scoring (no T/I yet) will have
substantially lower confidence than one with all three.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from decision.types import DecisionConfig


@dataclass(frozen=True)
class ModuleVerdict:
    """The per-module input to the composite. Optional fields stay
    None when that module has no current score for the ticker."""

    score: Optional[float] = None         # -100..+100
    confidence: Optional[float] = None    # 0..1
    model_version: Optional[str] = None


@dataclass(frozen=True)
class CompositeScore:
    score: float
    confidence: float
    contributing_modules: tuple[str, ...]
    per_module: dict[str, dict] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)


def score_composite(
    *,
    fundamental: ModuleVerdict,
    technical: ModuleVerdict,
    information: ModuleVerdict,
    config: Optional[DecisionConfig] = None,
    cluster_id: Optional[str] = None,
) -> CompositeScore:
    """Combine the three module verdicts into one composite.

    ``cluster_id``: when provided AND the cluster has learned weights
    in ``config.cluster_weight_overrides``, those weights are used;
    otherwise the global defaults are applied. Pass the result of
    :func:`training.registry.load_current_assignments` lookup at runtime.
    """
    config = config or DecisionConfig()
    weights = config.weights_for(cluster_id)

    contributions: dict[str, dict] = {}
    contributing: list[str] = []
    weighted_score_sum = 0.0
    weighted_conf_sum = 0.0
    effective_weight_sum = 0.0

    for code, mv in (("F", fundamental), ("T", technical), ("I", information)):
        if mv.score is None or mv.confidence is None:
            contributions[code] = {"present": False}
            continue
        w = weights[code]
        weighted_score_sum += mv.score * w
        weighted_conf_sum += mv.confidence * w
        effective_weight_sum += w
        contributing.append(code)
        contributions[code] = {
            "present": True, "score": mv.score, "confidence": mv.confidence,
            "weight": w, "model_version": mv.model_version,
        }

    if effective_weight_sum == 0:
        return CompositeScore(
            score=0.0, confidence=0.0,
            contributing_modules=(),
            per_module=contributions, weights_used=weights,
        )

    # Re-scale by effective weight so missing modules don't push the
    # score toward 0 — keep the signal of the modules that DO speak.
    composite_score = weighted_score_sum / effective_weight_sum * sum(weights.values())
    composite_score = max(-100.0, min(100.0, composite_score))

    # Confidence: weighted module confidence × coverage ratio
    coverage = effective_weight_sum / sum(weights.values()) if sum(weights.values()) else 0.0
    composite_confidence = (weighted_conf_sum / effective_weight_sum) * coverage
    composite_confidence = max(0.0, min(1.0, composite_confidence))

    return CompositeScore(
        score=composite_score,
        confidence=composite_confidence,
        contributing_modules=tuple(contributing),
        per_module=contributions,
        weights_used=weights,
    )
