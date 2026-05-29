"""Technical-module scorer.

Runs every enabled :class:`Signal` against an :class:`IndicatorContext`
and aggregates verdicts into a single score in [-100, +100].

Default aggregation: confidence-weighted mean of signal scores,
final confidence = ratio of contributing signals (those with
confidence > 0) to total enabled signals.

Result includes per-signal breakdown in `inputs` so a UI can show
"why" the technical score is what it is.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from technical.indicators import IndicatorContext
from technical.signals.base import Signal, SignalVerdict
from technical.signals.mean_reversion import MeanReversionSignal
from technical.signals.momentum import MomentumSignal
from technical.signals.red_green_signal import RedGreenSignal
from technical.signals.trend import TrendSignal


def default_signals() -> list[Signal]:
    """Production signal set."""
    return [
        MomentumSignal(),
        TrendSignal(),
        MeanReversionSignal(),
        RedGreenSignal(),
    ]


@dataclass(frozen=True)
class TechnicalScore:
    score: float
    confidence: float
    verdicts: list[SignalVerdict]

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "confidence": self.confidence,
            "signals": [
                {
                    "name": v.name, "score": v.score,
                    "confidence": v.confidence, "inputs": v.inputs,
                }
                for v in self.verdicts
            ],
        }


def score_technical(
    ctx: IndicatorContext,
    *,
    signals: Iterable[Signal] | None = None,
) -> TechnicalScore:
    sigs = list(signals) if signals is not None else default_signals()
    verdicts = [s.evaluate(ctx) for s in sigs]
    contributing = [v for v in verdicts if v.confidence > 0]

    if not contributing:
        return TechnicalScore(score=0.0, confidence=0.0, verdicts=verdicts)

    weight_sum = sum(v.confidence for v in contributing)
    weighted = sum(v.score * v.confidence for v in contributing)
    score = weighted / weight_sum
    confidence = len(contributing) / len(sigs)

    # Clamp to spec range
    score = max(-100.0, min(100.0, score))
    return TechnicalScore(score=score, confidence=confidence, verdicts=verdicts)
