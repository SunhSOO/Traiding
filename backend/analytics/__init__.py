"""Analytics — derived insights over decisions + trades.

Module performance attribution: which of the three module scores
(F/T/I) explained the most forward return on actual trades? Output
feeds the operator's "should we re-weight?" judgement *outside* the
automated training cycle, and lets us spot when one module has
gone stale (e.g. fundamentals haven't moved in a quarter and aren't
contributing)."""
from __future__ import annotations

from analytics.attribution import (
    AttributionResult, ModuleAttribution,
    compute_attribution,
)
from analytics.data_quality import (
    FreshnessScore, GapWindow,
    find_gaps, is_weekday, score_freshness,
)
from analytics.sector_rotation import (
    SectorRotationResult, compute_sector_rotation,
)

__all__ = [
    "AttributionResult", "ModuleAttribution", "compute_attribution",
    "FreshnessScore", "GapWindow",
    "find_gaps", "is_weekday", "score_freshness",
    "SectorRotationResult", "compute_sector_rotation",
]
