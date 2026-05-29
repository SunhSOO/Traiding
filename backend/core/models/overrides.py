"""Runtime cluster-weight overrides.

Operator-set per-cluster weight triples that take precedence over the
learned values in ``cluster_weights``. Use cases:

- Disabling a clearly mis-trained cluster ("force 50/50/0 until we
  retrain")
- A/B testing a hand-picked weight set against what training produced
- Emergency dampening when the live model starts misbehaving

The decision runner reads this table first, falling back to the
learned weights when no override exists. Audit-trail columns
(``set_by``, ``set_ts``, ``reason``) make it clear at a glance who
forced what and why."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAt


class ClusterWeightOverride(Base):
    """One row per cluster_id under operator control."""

    __tablename__ = "cluster_weight_overrides"
    __table_args__ = (
        Index("ix_cluster_weight_overrides_set_ts", "set_ts"),
    )

    cluster_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    w_fundamental: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    w_technical: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    w_information: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    set_by: Mapped[str] = mapped_column(String(64), nullable=False)
    set_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = CreatedAt
