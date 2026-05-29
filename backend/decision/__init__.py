"""Composite decision engine.

Reads the latest ModuleScore rows for each (market, ticker) and turns
them into a trading decision:

    F + T + I scores (each in [-100, +100])
        → weighted composite + per-module confidences
        → gates (min confidence, blackouts, churn limit)
        → action (BUY/SELL/HOLD/REJECTED)
        → sizer (Kelly-variant × volatility target × risk cap)
        → risk engine (Phase 0.5 — 6 hard limits)
        → broker (paper today; live in Phase 6+)
        → DecisionAudit + RiskSnapshot (Phase 0.6 — permanent record)

All five steps are independent + injectable for testing. The runner
in :mod:`decision.runner` wires them.
"""
from __future__ import annotations

from decision.composite import CompositeScore, ModuleVerdict, score_composite
from decision.gates import GateResult, run_gates
from decision.sizer import SizerInputs, SizerOutput, size_position
from decision.types import Action, DecisionConfig, DecisionVerdict

__all__ = [
    "Action", "DecisionConfig", "DecisionVerdict",
    "CompositeScore", "ModuleVerdict", "score_composite",
    "GateResult", "run_gates",
    "SizerInputs", "SizerOutput", "size_position",
]
