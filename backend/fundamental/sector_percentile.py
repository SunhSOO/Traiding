"""Within-sector ranking.

A 10% ROE is mediocre for tech (~20% sector median) but excellent
for utilities (~6% sector median). The fundamental scorer therefore
ranks each ticker's ratios WITHIN its sector before scoring.

For ratios where higher = better (ROE, op margin, growth) we use the
raw percentile rank. For ratios where lower = better (PER, PBR,
debt/equity) we invert: a low-PER stock gets a HIGH percentile.

Percentile is computed by ordinal rank (no interpolation) over the
non-None values in the sector. Missing inputs are returned as None.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Direction: "higher_better" = larger ratio → larger percentile.
# "lower_better" = smaller ratio → larger percentile (inverted rank).
RATIO_DIRECTION: dict[str, str] = {
    "per": "lower_better",
    "pbr": "lower_better",
    "roe": "higher_better",
    "roa": "higher_better",
    "op_margin": "higher_better",
    "net_margin": "higher_better",
    "debt_to_equity": "lower_better",
    "current_ratio": "higher_better",
    "fcf_yield": "higher_better",
    "revenue_growth_yoy": "higher_better",
    "earnings_growth_yoy": "higher_better",
}


@dataclass(frozen=True)
class Percentiles:
    """One ticker's percentiles within its sector. 0.0 = worst,
    1.0 = best. None when input was missing OR sector too small."""

    values: dict[str, Optional[float]]

    def get(self, ratio_name: str) -> Optional[float]:
        return self.values.get(ratio_name)


def percentile_within_sector(
    sector_ratios: dict[str, list[Optional[float]]],
    *,
    min_sector_size: int = 5,
) -> list[Percentiles]:
    """Compute per-ticker percentiles given a column-major view of
    one sector's ratios.

    Parameters
    ----------
    sector_ratios : dict
        Maps ratio_name → list of values, one per ticker, in fixed
        ticker order. None entries are tickers without that ratio.
    min_sector_size : int
        Sectors with fewer than this many tickers reporting a given
        ratio yield None for that ratio (statistics too noisy).

    Returns
    -------
    list[Percentiles]
        One Percentiles object per ticker, same order as input lists.
    """
    if not sector_ratios:
        return []

    # Validate equal-length lists
    n_tickers = len(next(iter(sector_ratios.values())))
    for name, vals in sector_ratios.items():
        if len(vals) != n_tickers:
            raise ValueError(f"ratio {name!r} list has wrong length: {len(vals)} != {n_tickers}")

    out: list[dict[str, Optional[float]]] = [{} for _ in range(n_tickers)]

    for ratio_name, values in sector_ratios.items():
        direction = RATIO_DIRECTION.get(ratio_name, "higher_better")
        defined = [(i, v) for i, v in enumerate(values) if v is not None]
        if len(defined) < min_sector_size:
            for i in range(n_tickers):
                out[i][ratio_name] = None
            continue

        sorted_defined = sorted(defined, key=lambda iv: iv[1])
        # Ordinal percentile: rank / (n - 1). Identical values share
        # the average rank.
        n = len(sorted_defined)
        rank_by_index: dict[int, float] = {}
        i = 0
        while i < n:
            j = i
            while j + 1 < n and sorted_defined[j + 1][1] == sorted_defined[i][1]:
                j += 1
            avg_rank = (i + j) / 2  # average of inclusive range
            pct = avg_rank / (n - 1) if n > 1 else 0.5
            for k in range(i, j + 1):
                idx = sorted_defined[k][0]
                rank_by_index[idx] = pct
            i = j + 1

        for i in range(n_tickers):
            if values[i] is None:
                out[i][ratio_name] = None
                continue
            p = rank_by_index[i]
            if direction == "lower_better":
                p = 1.0 - p
            out[i][ratio_name] = p

    return [Percentiles(values=v) for v in out]
