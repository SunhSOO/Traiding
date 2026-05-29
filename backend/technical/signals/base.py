"""Signal interface — one Signal = one self-contained technical idea.

Each Signal:
- Takes an IndicatorContext (cached OHLCV + indicators).
- Returns a :class:`SignalVerdict` with score in [-100, +100], confidence
  in [0, 1], and an `inputs` dict for audit.

Composition is done by :mod:`technical.score` — it runs all enabled
signals and aggregates them into a single Technical module score.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SignalVerdict:
    name: str
    score: float                                # -100..+100
    confidence: float                           # 0..1
    inputs: dict[str, Any] = field(default_factory=dict)


class Signal(ABC):
    """Concrete signals subclass this and implement :meth:`evaluate`."""

    name: str

    @abstractmethod
    def evaluate(self, ctx) -> SignalVerdict:
        """Compute the signal against ``ctx`` (an IndicatorContext)."""

    # Convenience for scoring code that wants None when there isn't
    # enough data — keeps Signal authors from sprinkling NaN handling.
    def _abstain(self, reason: str) -> SignalVerdict:
        return SignalVerdict(
            name=self.name, score=0.0, confidence=0.0,
            inputs={"abstain_reason": reason},
        )
