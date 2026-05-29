"""Sector-rotation scoring — pure math, no DB.

Big idea: sectors don't move uniformly. When energy is leading and
tech is lagging, an "energy ticker scoring +30" matters more than a
"tech ticker scoring +30". We compute a per-sector rotation score
from the cross-section of recent returns and surface a small
*contextual bonus* the decision engine can fold into composite
score.

Output is intentionally bounded ([-15, +15]) so it can't dominate
the F/T/I composite (which lives in [-100, +100]). Think of it as
a tie-breaker for ambiguous mid-band signals, not a primary driver.

Two inputs:

- ``recent_returns`` — per-ticker rolling-window return (typically
  20-day). Caller computes from ``daily_prices``.
- ``ticker_to_sector`` — mapping from ``Security.sector``.

Pipeline:

1. Group returns by sector → take median return per sector
   (median is more robust to one outlier ticker than mean).
2. Rank sectors by median return.
3. Within each sector, every ticker gets a sector bonus equal to
   the sector's *z-score across sectors* clipped to [-1.5, +1.5]
   then scaled to ±15.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass(frozen=True)
class SectorRotationResult:
    """Output: per-sector rotation score + per-ticker bonus."""
    sector_scores: dict[str, float] = field(default_factory=dict)
    ticker_bonus: dict[str, float] = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)


def compute_sector_rotation(
    *,
    recent_returns: dict[str, float],
    ticker_to_sector: dict[str, str],
    min_tickers_per_sector: int = 3,
    bonus_cap: float = 15.0,
) -> SectorRotationResult:
    """Run the rotation calculation.

    Parameters
    ----------
    recent_returns
        Per-ticker rolling return (e.g. 20-day pct change). Tickers
        with no entry are silently dropped.
    ticker_to_sector
        ``Security.sector`` lookup. Tickers without a sector are
        skipped — we never attach bonuses to unclassified names.
    min_tickers_per_sector
        Sectors with fewer than this many tickers don't get a score.
        Single-ticker "sectors" are noise, not signal.
    bonus_cap
        Absolute cap on the per-ticker bonus. Default 15 so that even
        a 3-sigma outlier sector adds at most ±15 to a F/T/I composite
        living in [-100, +100].
    """
    # 1. Group by sector
    by_sector: dict[str, list[float]] = {}
    for ticker, r in recent_returns.items():
        sec = ticker_to_sector.get(ticker)
        if sec is None or r is None:
            continue
        try:
            by_sector.setdefault(sec, []).append(float(r))
        except (TypeError, ValueError):
            continue

    if not by_sector:
        return SectorRotationResult(diagnostics={"reason": "no_sector_data"})

    # 2. Sector median
    sector_median: dict[str, float] = {}
    sector_sample_size: dict[str, int] = {}
    for sec, rs in by_sector.items():
        if len(rs) < min_tickers_per_sector:
            continue
        sector_median[sec] = statistics.median(rs)
        sector_sample_size[sec] = len(rs)

    if len(sector_median) < 2:
        return SectorRotationResult(
            sector_scores=sector_median,
            diagnostics={
                "reason": "insufficient_sectors_for_zscore",
                "sample_sizes": sector_sample_size,
            },
        )

    # 3. Z-score across sectors. Use population stdev for stability
    # in small-sector-count environments.
    values = list(sector_median.values())
    mean_med = sum(values) / len(values)
    var = sum((v - mean_med) ** 2 for v in values) / len(values)
    stdev = math.sqrt(var) if var > 0 else 0.0

    sector_z: dict[str, float] = {}
    ticker_bonus: dict[str, float] = {}
    if stdev <= 0:
        # All sectors equal — bonus is zero everywhere.
        return SectorRotationResult(
            sector_scores=sector_median,
            diagnostics={
                "reason": "zero_cross_sector_variance",
                "mean_median_return": mean_med,
            },
        )

    for sec, med in sector_median.items():
        z = (med - mean_med) / stdev
        clipped = max(-1.5, min(1.5, z))
        sector_z[sec] = clipped * (bonus_cap / 1.5)   # scale to ±bonus_cap

    # Per-ticker bonus = its sector's bonus
    for ticker, sec in ticker_to_sector.items():
        b = sector_z.get(sec)
        if b is not None:
            ticker_bonus[ticker] = b

    return SectorRotationResult(
        sector_scores=sector_z,
        ticker_bonus=ticker_bonus,
        diagnostics={
            "sample_sizes": sector_sample_size,
            "mean_median_return": mean_med,
            "stdev_median_return": stdev,
        },
    )
