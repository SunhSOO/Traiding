"""Per-module analysis scores.

Each row is one module's verdict on one ticker at one point in time:

    (computed_ts, market, ticker, module)
        → score (-100..+100), confidence (0..1)

The composite decision engine in Phase 4 reads the latest row per
(market, ticker, module), combines the three module scores with
per-ticker learned weights, and emits a trading decision.

We store every computation, not just the latest, so:
- Backtest can replay (composite at past T uses scores as-of T).
- Drift detection compares latest distribution to historical.
- Audit trail is preserved if a model retraining changes a score
  ex-post: each retrain writes new rows with a fresh ``model_version``.

Inputs to the score (raw indicator values, ratio numbers, LLM JSON)
go into ``inputs`` JSONB so a Phase-5 UI can "explain" any score by
clicking it. We cap JSONB sizes via the caller (~4KB rule) so the
table doesn't bloat.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Index, Numeric, PrimaryKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, market_column


class ModuleScore(Base):
    """One module's score for one (ticker, ts).

    ``module`` values:
        F — Fundamental
        T — Technical
        I — Information
    """

    __tablename__ = "module_scores"
    __table_args__ = (
        PrimaryKeyConstraint(
            "computed_ts", "market", "ticker", "module",
            name="pk_module_scores",
        ),
        Index("ix_module_scores_ticker_ts", "market", "ticker", "computed_ts"),
        Index("ix_module_scores_module_ts", "module", "computed_ts"),
    )

    computed_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        doc="The `as_of` instant the score was computed against. Hypertable partition key.",
    )
    market: Mapped[str] = market_column()
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    module: Mapped[str] = mapped_column(
        String(1), nullable=False, doc="F (fundamental) | T (technical) | I (information)"
    )

    score: Mapped[float] = mapped_column(
        Numeric(6, 3), nullable=False,
        doc="-100.000 .. +100.000 — negative = bearish, positive = bullish",
    )
    confidence: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False,
        doc="0.0000 .. 1.0000 — 0 = no data / unreliable, 1 = high-quality inputs all present",
    )

    model_version: Mapped[Optional[str]] = mapped_column(
        String(64), doc="Optional tag for ML-derived scores (Phase 3+)"
    )
    inputs: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        doc="Raw values that fed into the score, ≤ 4 KB. Subject to LLM "
            "output for the I module; ratio dict for F; per-signal breakdown for T.",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
