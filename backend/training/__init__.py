"""Phase 3 — learn per-cluster (w_F, w_T, w_I) weights.

Replaces the fixed weights in :class:`decision.types.DecisionConfig`
with values fitted from historical (module_scores → forward return)
pairs. Heavy ML libraries (LightGBM/vectorbt/MLflow) are kept in the
``ml`` dependency-group; this base implementation uses only numpy +
SQLAlchemy so it runs on a plain ``uv sync``.

End-to-end:

    securities + daily_prices  ────────────►  clusterer  ─►  ticker_clusters
    module_scores + daily_prices  ─►  labels  ─►  walk_forward splits  ─►
    trainer (OLS via lstsq) ─► registry ─► cluster_weights
                                  │
    decision/composite.py reads  ◄┘  latest row per cluster_id at runtime
"""
from __future__ import annotations

# Only re-export the dependency-free types here. DB-coupled functions
# (labels, clusterer, registry, runner) must be imported from their
# concrete modules — that way tests + tools that don't need SQLAlchemy
# can use the type layer without a forced import.
from training.types import (
    ClusterAssignment, ClusterMetrics, TrainResult, TrainSample, WalkForwardSplit,
)

__all__ = [
    "ClusterAssignment", "ClusterMetrics", "TrainResult",
    "TrainSample", "WalkForwardSplit",
]
