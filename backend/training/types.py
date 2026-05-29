"""Pure value types used by the training pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class TrainSample:
    """One (input, target) example for the OLS regression.

    ``f_score`` etc are in [-100, +100]. ``target`` is a forward return
    (decimal — 0.05 = +5%). Confidences are passed through so the
    trainer can optionally weight samples by reliability."""

    market: str
    ticker: str
    score_ts: datetime
    f_score: float
    t_score: float
    i_score: float
    f_confidence: float
    t_confidence: float
    i_confidence: float
    target: float                       # forward return


@dataclass(frozen=True)
class ClusterAssignment:
    market: str
    ticker: str
    cluster_id: str
    features: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WalkForwardSplit:
    """Train on [train_start, train_end); test on [test_start, test_end)."""

    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime

    @property
    def label(self) -> str:
        return (
            f"{self.train_start.date().isoformat()}-"
            f"{self.train_end.date().isoformat()}_to_"
            f"{self.test_start.date().isoformat()}-"
            f"{self.test_end.date().isoformat()}"
        )


@dataclass(frozen=True)
class ClusterMetrics:
    n_samples: int
    n_tickers: int
    r2_in_sample: float
    r2_walk_forward: Optional[float] = None
    hit_rate: Optional[float] = None
    notes: str = ""


@dataclass(frozen=True)
class TrainResult:
    """Output of fitting one cluster."""

    cluster_id: str
    w_fundamental: float
    w_technical: float
    w_information: float
    intercept: float
    metrics: ClusterMetrics
    model_version: str
