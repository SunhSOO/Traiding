"""Fundamental scorer — sector percentiles → [-100, +100].

Default weights (sum to 1.0):

    valuation:    PER 0.10, PBR 0.05, FCF yield 0.10        — 0.25
    quality:      ROE 0.15, ROA 0.05, op_margin 0.05         — 0.25
    leverage:     debt_to_equity 0.10, current_ratio 0.05    — 0.15
    growth:       revenue_yoy 0.15, earnings_yoy 0.20        — 0.35

Each ratio's contribution is (percentile - 0.5) * 200 * weight.
Sum across all defined ratios → final score in roughly [-100, +100],
then clamped.

Confidence = sum of weights of defined ratios.
"""
from __future__ import annotations

from dataclasses import dataclass

from fundamental.sector_percentile import Percentiles


DEFAULT_WEIGHTS: dict[str, float] = {
    "per": 0.10,
    "pbr": 0.05,
    "fcf_yield": 0.10,
    "roe": 0.15,
    "roa": 0.05,
    "op_margin": 0.05,
    "debt_to_equity": 0.10,
    "current_ratio": 0.05,
    "revenue_growth_yoy": 0.15,
    "earnings_growth_yoy": 0.20,
}


@dataclass(frozen=True)
class FundamentalScore:
    score: float
    confidence: float
    breakdown: dict[str, float]    # ratio_name → contribution to score

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "confidence": self.confidence,
            "breakdown": self.breakdown,
        }


def score_fundamental(
    pct: Percentiles,
    *,
    weights: dict[str, float] | None = None,
) -> FundamentalScore:
    """One ticker → FundamentalScore using its already-computed sector
    percentiles."""
    w = weights or DEFAULT_WEIGHTS
    total_weight = 0.0
    score_sum = 0.0
    breakdown: dict[str, float] = {}

    for name, weight in w.items():
        p = pct.get(name)
        if p is None:
            continue
        # (percentile - 0.5) maps [0,1] → [-0.5, +0.5]
        # × 200 → [-100, +100]
        # × weight → weighted contribution
        contrib = (p - 0.5) * 200.0 * weight
        breakdown[name] = contrib
        score_sum += contrib
        total_weight += weight

    if total_weight == 0:
        return FundamentalScore(score=0.0, confidence=0.0, breakdown={})

    # Re-scale so missing ratios don't dilute the score
    score = score_sum / total_weight * sum(w.values())
    score = max(-100.0, min(100.0, score))
    confidence = total_weight / sum(w.values())
    return FundamentalScore(score=score, confidence=confidence, breakdown=breakdown)
