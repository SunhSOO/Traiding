"""Canonical enums + classification value type for the information module.

The classifier enforces these enums via Ollama's JSON-schema mode so
free-form LLM output never reaches the rest of the system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventType(str, Enum):
    EARNINGS = "EARNINGS"          # results, revenue/EPS announcement
    GUIDANCE = "GUIDANCE"          # forward-looking forecast update
    M_AND_A = "M_AND_A"            # acquisition, merger, divestiture
    REGULATORY = "REGULATORY"      # FTC/antitrust/SEC action, sanctions
    MANAGEMENT = "MANAGEMENT"      # CEO/CFO change, board moves
    PRODUCT = "PRODUCT"            # launch, recall, technology
    MACRO = "MACRO"                # rates/FX/policy news with sector impact
    INSIDER_TX = "INSIDER_TX"      # insider buy/sell disclosure
    OTHER = "OTHER"


class Sentiment(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"

    @property
    def direction(self) -> int:
        """+1 / 0 / −1 — multiplier in scoring."""
        return {"POSITIVE": 1, "NEUTRAL": 0, "NEGATIVE": -1}[self.value]


class Impact(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def magnitude(self) -> float:
        """Multiplier in scoring — high-impact news weighs more."""
        return {"HIGH": 1.0, "MEDIUM": 0.5, "LOW": 0.2}[self.value]


class Horizon(str, Enum):
    INTRADAY = "INTRADAY"
    SHORT_TERM = "SHORT_TERM"
    MEDIUM_TERM = "MEDIUM_TERM"
    LONG_TERM = "LONG_TERM"

    @property
    def decay_days(self) -> float:
        """Time-decay half-life in days; longer-horizon news decays slower."""
        return {"INTRADAY": 1.0, "SHORT_TERM": 5.0,
                "MEDIUM_TERM": 21.0, "LONG_TERM": 90.0}[self.value]


@dataclass(frozen=True)
class ArticleClassification:
    """Normalised LLM output. Distinct from the ORM row of the same
    name in core.models.classifications — that's the persistence
    shape; this is the in-memory value type."""

    event_type: EventType
    sentiment: Sentiment
    impact: Impact
    horizon: Horizon
    confidence: float                          # 0..1
    summary: str = ""
    tickers_mentioned: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "sentiment": self.sentiment.value,
            "impact": self.impact.value,
            "horizon": self.horizon.value,
            "confidence": self.confidence,
            "summary": self.summary,
            "tickers_mentioned": list(self.tickers_mentioned),
        }
