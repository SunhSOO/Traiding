"""Pre-decision gates — reasons NOT to trade.

Each gate is a small pure function. If ALL gates pass, the decision
proceeds to sizing + risk + broker. If any gate fails, the decision
is HOLD with the failure reason captured in the audit row.

Gates here are higher-level than the 6 risk limits (Phase 0.5):
those run AFTER we've decided we want to trade and check account /
exposure constraints. Gates here run BEFORE — they ask "should we
even think about trading?".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from decision.composite import CompositeScore
from decision.types import DecisionConfig


@dataclass(frozen=True)
class GateResult:
    all_passed: bool
    failed_gates: tuple[str, ...]
    notes: dict = field(default_factory=dict)


def run_gates(
    *,
    composite: CompositeScore,
    config: DecisionConfig,
    last_decision_ts: Optional[datetime] = None,
    now: Optional[datetime] = None,
    score_max_age_hours: Optional[float] = None,
) -> GateResult:
    """Apply every gate. Returns aggregated pass/fail + per-gate notes.

    Parameters
    ----------
    last_decision_ts : datetime, optional
        Timestamp of the previous decision on this ticker. None for first.
    now : datetime, optional
        Used to compute age windows. Defaults to ``datetime.utcnow()``
        — caller should pin in backtests.
    score_max_age_hours : float, optional
        How stale the FRESHEST input module score may be. Caller
        computes this from the latest module_score.computed_ts; we
        compare to config.score_staleness_hours.
    """
    from datetime import UTC

    now = now or datetime.now(UTC)
    failed: list[str] = []
    notes: dict = {}

    # Gate 1: composite produced anything
    if not composite.contributing_modules:
        failed.append("no_module_scores")
        notes["no_module_scores"] = "No module produced a score for this ticker."

    # Gate 2: per-module confidence floor
    weak_modules = [
        code for code, info in composite.per_module.items()
        if info.get("present") and info.get("confidence", 0) < config.min_module_confidence
    ]
    if weak_modules:
        failed.append("module_confidence_below_floor")
        notes["module_confidence_below_floor"] = {
            "weak_modules": weak_modules,
            "floor": config.min_module_confidence,
        }

    # Gate 3: overall composite confidence floor
    if composite.confidence < config.min_overall_confidence:
        failed.append("composite_confidence_below_floor")
        notes["composite_confidence_below_floor"] = {
            "observed": composite.confidence,
            "floor": config.min_overall_confidence,
        }

    # Gate 4: churn — don't act on same ticker again within window
    if last_decision_ts is not None:
        age_minutes = (now - last_decision_ts).total_seconds() / 60.0
        if age_minutes < config.churn_minutes:
            failed.append("churn_window")
            notes["churn_window"] = {
                "minutes_since_last": age_minutes,
                "min_minutes_between": config.churn_minutes,
            }

    # Gate 5: stale score
    if score_max_age_hours is not None and score_max_age_hours > config.score_staleness_hours:
        failed.append("score_stale")
        notes["score_stale"] = {
            "score_age_hours": score_max_age_hours,
            "max_age_hours": config.score_staleness_hours,
        }

    return GateResult(
        all_passed=not failed,
        failed_gates=tuple(failed),
        notes=notes,
    )
