"""Drift detection — flag abnormal decisions vs historical distribution.

Two cheap checks today:

1. **Per-ticker score z-score**: if today's composite is > N standard
   deviations from the ticker's recent rolling mean, the system
   surfaces it as ANOMALOUS. The runner doesn't BLOCK on z-score
   (that's the operator's job to interpret); it just flags so the
   audit row shows ``drift_anomaly=true`` and ops dashboards can
   alert.

2. **Per-market action-mix shift**: if today's BUY/SELL/HOLD mix
   differs materially from the 30-day rolling mix, log a market-wide
   drift warning. Useful for catching "model accidentally turned
   everyone bullish" failure modes.

These signals are advisory; Phase 4.2 (later) might wire them into
kill-switch logic. For now they're observability.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ScoreDriftCheck:
    is_anomaly: bool
    zscore: float
    mean: float
    stdev: float
    n_history: int


def check_score_drift(
    *,
    current_score: float,
    history: Iterable[float],
    z_threshold: float = 3.0,
    min_history: int = 10,
) -> ScoreDriftCheck:
    """Return a z-score check vs recent history.

    Treats `is_anomaly=False` when history is too short to trust —
    new tickers don't have a baseline.
    """
    hist = list(history)
    n = len(hist)
    if n < min_history:
        return ScoreDriftCheck(
            is_anomaly=False, zscore=0.0, mean=0.0, stdev=0.0, n_history=n,
        )
    mean = sum(hist) / n
    variance = sum((x - mean) ** 2 for x in hist) / n
    stdev = math.sqrt(variance) if variance > 0 else 0.0
    if stdev == 0:
        # Degenerate case — if every prior score was identical and
        # today's differs, that IS an anomaly.
        is_anomaly = current_score != mean
        return ScoreDriftCheck(
            is_anomaly=is_anomaly, zscore=0.0, mean=mean, stdev=0.0, n_history=n,
        )
    z = (current_score - mean) / stdev
    return ScoreDriftCheck(
        is_anomaly=abs(z) >= z_threshold,
        zscore=z, mean=mean, stdev=stdev, n_history=n,
    )


@dataclass(frozen=True)
class ActionMixDriftCheck:
    is_anomaly: bool
    today_distribution: dict[str, float]
    history_distribution: dict[str, float]
    total_variation_distance: float


def check_action_mix_drift(
    *,
    today_actions: Iterable[str],
    history_actions: Iterable[str],
    tv_threshold: float = 0.25,
    min_today: int = 20,
) -> ActionMixDriftCheck:
    """Compare today's action distribution to history; flag if total-
    variation distance exceeds threshold.

    Total variation = ½ Σ |p_today(a) - p_history(a)| over actions.
    Values 0..1; 0 = identical, 1 = no overlap.
    """
    today = list(today_actions)
    hist = list(history_actions)
    if len(today) < min_today or not hist:
        return ActionMixDriftCheck(
            is_anomaly=False, today_distribution={}, history_distribution={},
            total_variation_distance=0.0,
        )

    today_p = _normalise(Counter(today))
    hist_p = _normalise(Counter(hist))
    actions = set(today_p) | set(hist_p)
    tv = sum(abs(today_p.get(a, 0) - hist_p.get(a, 0)) for a in actions) / 2.0

    return ActionMixDriftCheck(
        is_anomaly=tv >= tv_threshold,
        today_distribution=today_p,
        history_distribution=hist_p,
        total_variation_distance=tv,
    )


def _normalise(counter: Counter) -> dict[str, float]:
    total = sum(counter.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in counter.items()}
