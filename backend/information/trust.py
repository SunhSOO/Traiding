"""Source-trust weighting + time decay.

Different sources have different reliability for trading signals:

| Source           | Trust | Rationale                                 |
|------------------|-------|-------------------------------------------|
| DART / EDGAR     | 1.00  | Authoritative regulator filings           |
| BIGKinds (major) | 0.85  | Curated press archive, verified outlets   |
| RSS (major press)| 0.75  | Direct from publishers, no aggregation    |
| Naver Search     | 0.70  | Broad coverage, mixed publisher tier      |
| GDELT            | 0.55  | Automated discovery, includes minor blogs |
| Wayback          | 0.50  | Recovered context, content may be stale   |

These multiply the LLM impact × sentiment when aggregating into
a ticker score. Source-trust is configurable so an operator who finds
e.g. Naver too noisy can dial it down without code changes.

Time decay: news effects fade. We apply exponential decay with the
half-life set by the article's classified ``horizon``:

    weight(age) = trust × 2^(-age_days / horizon.decay_days)

After ~5 half-lives the contribution is ~3% of the initial weight,
so we cap the relevance window at 5 × horizon.decay_days days when
querying — anything older isn't worth loading.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import pow

from information.types import Horizon


DEFAULT_SOURCE_TRUST: dict[str, float] = {
    "dart": 1.00,
    "edgar": 1.00,
    "kind": 1.00,
    "bigkinds": 0.85,
    "naver_search": 0.70,
    "gdelt": 0.55,
    "wayback": 0.50,
}


def source_trust(source: str, *, overrides: dict[str, float] | None = None) -> float:
    """Look up trust by source name. RSS sources fall back to a sensible
    default based on the host (RSS feeds carry source='rss:<host>')."""
    table = {**DEFAULT_SOURCE_TRUST, **(overrides or {})}
    if source in table:
        return table[source]
    if source.startswith("rss:"):
        host = source.split(":", 1)[1]
        # Use the specific RSS override if present, else a generic default.
        return table.get(source, table.get(f"rss:{host}", 0.75))
    return 0.5  # unknown source


def time_decay_weight(age_days: float, horizon: Horizon) -> float:
    """Exponential decay; returns 1.0 at age 0, 0.5 at one half-life."""
    if age_days < 0:
        age_days = 0
    half_life = horizon.decay_days
    return pow(0.5, age_days / half_life) if half_life > 0 else 0.0


@dataclass(frozen=True)
class WeightedMention:
    """One classified article's contribution to one ticker's score."""

    ticker: str
    market: str
    direction: int                    # +1 / 0 / -1 (from sentiment)
    impact_magnitude: float           # 0.2 / 0.5 / 1.0 (from impact)
    confidence: float                 # 0..1 (LLM)
    source_trust: float               # 0..1 (this module)
    time_weight: float                # 0..1 (this module)

    @property
    def signed_weight(self) -> float:
        """Combined signed contribution. Multiply by 100 to get the
        ticker-score units."""
        return (
            self.direction
            * self.impact_magnitude
            * self.confidence
            * self.source_trust
            * self.time_weight
        )
