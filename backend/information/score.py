"""Information score — aggregate classified mentions into a per-ticker
score in [-100, +100].

Inputs: a list of :class:`WeightedMention` for ONE ticker over a
lookback window. Output: :class:`InformationScore` (score + confidence
+ inputs for audit).

The score is a weighted sum of signed contributions, then squashed
through ``tanh`` to keep the output in [-100, +100] regardless of how
many mentions accumulate. We use tanh rather than clip so a ticker
with 5 strong negative mentions still scores worse than one with 2.

Confidence is the sum of effective weights normalised so that a
small handful of high-quality recent items can already reach 1.0,
while a lot of weak old items still tops out around 0.6.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from information.trust import WeightedMention


@dataclass(frozen=True)
class InformationScore:
    score: float
    confidence: float
    n_mentions: int
    breakdown: dict


def score_information(
    mentions: Iterable[WeightedMention],
    *,
    saturation_weight: float = 5.0,
) -> InformationScore:
    """Aggregate ``mentions`` into one ticker score.

    Parameters
    ----------
    saturation_weight : float
        The "this many high-quality mentions = full confidence" scale.
        Lower means it's easier to hit high confidence; higher means
        we want a thicker stack of evidence first. 5.0 is the
        default for daily-cadence trading.
    """
    mentions = list(mentions)
    if not mentions:
        return InformationScore(score=0.0, confidence=0.0, n_mentions=0,
                                breakdown={"reason": "no mentions"})

    signed_sum = 0.0
    abs_weight_sum = 0.0
    pos_count = neg_count = neu_count = 0

    for m in mentions:
        signed_sum += m.signed_weight
        abs_weight_sum += abs(m.impact_magnitude * m.confidence * m.source_trust * m.time_weight)
        if m.direction > 0:
            pos_count += 1
        elif m.direction < 0:
            neg_count += 1
        else:
            neu_count += 1

    # tanh-squash signed_sum into [-100, +100]. Scale picks where the
    # squash starts to saturate.
    score = 100.0 * math.tanh(signed_sum / saturation_weight)
    score = max(-100.0, min(100.0, score))

    # Confidence: 1 - exp(-abs_weight / saturation) — same saturation point.
    confidence = 1.0 - math.exp(-abs_weight_sum / saturation_weight)
    confidence = max(0.0, min(1.0, confidence))

    return InformationScore(
        score=score, confidence=confidence, n_mentions=len(mentions),
        breakdown={
            "signed_sum": signed_sum, "abs_weight_sum": abs_weight_sum,
            "positive_mentions": pos_count, "neutral_mentions": neu_count,
            "negative_mentions": neg_count,
        },
    )
