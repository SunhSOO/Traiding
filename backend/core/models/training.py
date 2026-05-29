"""Training-related tables.

Two tables:

1. ``ticker_clusters`` — which cluster a ticker belongs to. Append-
   only with ``assigned_at`` so we can track reassignments (a ticker
   moves between buckets as its market cap / sector changes).
2. ``cluster_weights`` — learned (w_F, w_T, w_I) per cluster per
   training run. ``learned_at`` doubles as the version id; the
   decision engine reads the latest row per cluster at runtime.

We deliberately keep both tables small (one row per ticker per
reassignment, ~10-20 clusters × ~1 retraining/week) so neither is a
hypertable. They live in regular PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, market_column


class TickerClusterAssignment(Base):
    """Append-only record: this ticker was in cluster X starting at T.

    The runtime helper resolves "current cluster of (market, ticker)"
    by taking the row with the latest ``assigned_at``.
    """

    __tablename__ = "ticker_clusters"
    __table_args__ = (
        PrimaryKeyConstraint(
            "market", "ticker", "assigned_at", name="pk_ticker_clusters",
        ),
        Index("ix_ticker_clusters_cluster", "cluster_id"),
    )

    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    cluster_id: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="Cluster id of the form '<market>:<sector>:<size>' or learned label.",
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    features: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        doc="Feature vector used to assign — e.g. sector, market_cap_bucket, "
            "30d-volatility-bucket. Audit only; not authoritative.",
    )


class ClusterWeights(Base):
    """One training run's verdict on (w_F, w_T, w_I) for one cluster.

    ``learned_at`` is the de-facto version. Decision runner takes the
    latest row per cluster_id.

    Metrics live in JSONB so future trainers can add e.g.
    ``r2_train``, ``r2_test``, ``walk_forward_sharpe``, ``hit_rate``.
    """

    __tablename__ = "cluster_weights"
    __table_args__ = (
        PrimaryKeyConstraint(
            "cluster_id", "learned_at", name="pk_cluster_weights",
        ),
        Index("ix_cluster_weights_cluster_latest", "cluster_id", "learned_at"),
    )

    cluster_id: Mapped[str] = mapped_column(String(64), nullable=False)
    learned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    w_fundamental: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False)
    w_technical: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False)
    w_information: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False)
    intercept: Mapped[float] = mapped_column(
        Numeric(10, 6), nullable=False, default=0.0,
        doc="OLS intercept; informational. Composite never uses it directly.",
    )

    n_samples: Mapped[int] = mapped_column()
    n_tickers: Mapped[int] = mapped_column()

    model_version: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="e.g. 'ols-v1:2026-05-29' — tag for compatibility.",
    )
    metrics: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    notes: Mapped[Optional[str]] = mapped_column(Text)
