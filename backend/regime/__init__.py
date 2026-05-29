"""Market regime classification.

A regime label (``RISK_ON`` / ``NEUTRAL`` / ``RISK_OFF``) tags the
prevailing macro stance for a given date so downstream consumers
(decision threshold scaling, sizer caution, drift signals) have a
single coherent state to reason about.

The classifier is fully data-driven (no LLM): it reads the macro
series we already ingest (VIX, FX_DXY, RATE_US_10Y, IDX_KOSPI) plus
a derived "trend vs 200d SMA" flag for the S&P/KOSPI proxy. Each
input contributes a directional vote; the majority becomes the
regime label, with confidence proportional to vote agreement.

This is the *current* regime detector — a future iteration could
swap in HMMs, Bayesian regime-switching, or LLM-driven narrative
classification. The interface is stable; only the implementation
moves."""
from __future__ import annotations

from regime.classifier import (
    RegimeLabel,
    RegimeVote,
    RegimeReading,
    classify_regime,
    trend_vs_sma,
)

__all__ = [
    "RegimeLabel",
    "RegimeVote",
    "RegimeReading",
    "classify_regime",
    "trend_vs_sma",
]
