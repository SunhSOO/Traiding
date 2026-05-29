"""Backtest replay harness.

Reusable building blocks:

- ``replay`` — pure simulator. Given a list of historical decision
  signals (timestamp + action) and a price-lookup callable, walks
  them in time order, opens/closes virtual positions, and emits
  closed-trade records compatible with ``brokers.paper_equity``.

- ``runner`` — thin wrapper that loads DecisionAudit + DailyPrice
  rows from the DB and feeds them into ``replay``. Kept separate so
  the simulator can be unit-tested without a DB.

What this is NOT:
- It is NOT a per-day re-scoring engine. The "would current weights
  have changed past decisions" question is a separate harness and
  needs historical module_scores + as_of replay (Phase 6.x).
- It does NOT model slippage, commission, or tax beyond what the
  caller bakes into the price oracle. We accept that price-only
  replay is optimistic; the goal here is to validate the *signal*,
  not the *execution*.
"""
from __future__ import annotations

from backtest.replay import (
    DecisionSignal, ReplayResult, SimulatedTrade, replay,
)

__all__ = ["DecisionSignal", "ReplayResult", "SimulatedTrade", "replay"]
