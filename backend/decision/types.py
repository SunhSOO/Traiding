"""Public decision types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"          # close-long or open-short depending on broker
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    REJECTED = "REJECTED"  # passed all logic but blocked at risk engine


@dataclass(frozen=True)
class DecisionConfig:
    """Knobs the operator can tune per environment.

    Default values are conservative — they bias toward HOLD when
    inputs are weak so paper trading doesn't generate noise."""

    # ── Composite weights ──
    weight_fundamental: float = 0.35
    weight_technical: float = 0.40
    weight_information: float = 0.25

    # ── Thresholds ──
    buy_threshold: float = 25.0          # composite > this → BUY consideration
    sell_threshold: float = -25.0        # composite < this → SELL/EXIT consideration

    # ── Gates ──
    min_module_confidence: float = 0.30  # any module below → abstain
    min_overall_confidence: float = 0.40
    churn_minutes: int = 60              # no new decision on same ticker within window
    score_staleness_hours: int = 48      # if module score older than this → abstain

    # ── Sizer ──
    base_position_fraction: float = 0.05   # base trade = 5% of account equity
    target_volatility_bps: float = 200.0   # target 2% per-position daily vol
    max_position_fraction: float = 0.20    # hard cap per position

    # ── Learned weights (Phase 3 — populated at runtime from cluster_weights) ──
    # Map cluster_id → {"F", "T", "I"} per-cluster weights. Empty dict = use
    # the global ``weight_*`` fields above (back-compat for paper-mode).
    cluster_weight_overrides: dict[str, dict[str, float]] = field(default_factory=dict)

    # ── Regime-aware adjustment (Phase 5+) ──
    # Per-regime scalers applied AFTER the base composite is computed.
    #
    # * ``regime_threshold_scalers[regime]["buy"]``  → multiply ``buy_threshold``
    # * ``regime_threshold_scalers[regime]["sell"]`` → multiply (-1)*sell_threshold
    #   (sell_threshold itself is negative; we scale its magnitude)
    # * ``regime_size_scalers[regime]`` → multiply ``base_position_fraction``
    #
    # Default behaviour: RISK_OFF tightens BUY + relaxes SELL + shrinks size;
    # RISK_ON loosens BUY + tightens SELL + keeps full size; NEUTRAL is no-op.
    # Set both dicts to ``{}`` (empty) to disable — the runner falls back to
    # raw thresholds and the global ``base_position_fraction``.
    regime_threshold_scalers: dict[str, dict[str, float]] = field(default_factory=lambda: {
        "RISK_ON":  {"buy": 0.85, "sell": 1.15},
        "NEUTRAL":  {"buy": 1.00, "sell": 1.00},
        "RISK_OFF": {"buy": 1.30, "sell": 0.80},
    })
    regime_size_scalers: dict[str, float] = field(default_factory=lambda: {
        "RISK_ON":  1.00,
        "NEUTRAL":  0.85,
        "RISK_OFF": 0.50,
    })

    def buy_threshold_for_regime(self, regime: Optional[str]) -> float:
        """Effective BUY threshold under ``regime`` (RISK_ON / NEUTRAL /
        RISK_OFF or None). None or unknown labels fall back to the
        unscaled value."""
        if not regime:
            return self.buy_threshold
        scaler = self.regime_threshold_scalers.get(regime, {}).get("buy", 1.0)
        return self.buy_threshold * scaler

    def sell_threshold_for_regime(self, regime: Optional[str]) -> float:
        """Effective SELL threshold under ``regime``. Sell threshold is
        negative; the scaler multiplies its MAGNITUDE so a >1 scaler
        means "harder to trigger a SELL"."""
        if not regime:
            return self.sell_threshold
        scaler = self.regime_threshold_scalers.get(regime, {}).get("sell", 1.0)
        # sell_threshold is negative; magnitude scales:
        return self.sell_threshold * scaler

    def size_fraction_for_regime(self, regime: Optional[str]) -> float:
        if not regime:
            return self.base_position_fraction
        scaler = self.regime_size_scalers.get(regime, 1.0)
        return self.base_position_fraction * scaler

    def normalised_weights(self) -> dict[str, float]:
        """Re-normalise so the three weights sum to 1.0 (any can be 0)."""
        total = self.weight_fundamental + self.weight_technical + self.weight_information
        if total <= 0:
            return {"F": 1 / 3, "T": 1 / 3, "I": 1 / 3}
        return {
            "F": self.weight_fundamental / total,
            "T": self.weight_technical / total,
            "I": self.weight_information / total,
        }

    def weights_for(self, cluster_id: Optional[str] = None) -> dict[str, float]:
        """Resolve the weight triple for one decision.

        Returns the learned per-cluster weights when present, else the
        global default. Callers (decision/composite + runner) only ever
        go through this method; per-cluster wiring is invisible to them.
        """
        if cluster_id and cluster_id in self.cluster_weight_overrides:
            w = self.cluster_weight_overrides[cluster_id]
            # Re-normalise defensively in case the registry stored
            # un-normalised numbers.
            total = sum(w.get(k, 0.0) for k in ("F", "T", "I"))
            if total > 0:
                return {k: w.get(k, 0.0) / total for k in ("F", "T", "I")}
        return self.normalised_weights()


@dataclass(frozen=True)
class DecisionVerdict:
    """The end-to-end verdict the runner produces per ticker. Drives
    the audit row and the broker call (if action requires execution)."""

    action: Action
    composite_score: Optional[float] = None
    composite_confidence: Optional[float] = None
    size_value: Optional[float] = None
    size_currency: Optional[str] = None
    reason: str = ""
    gate_results: dict = field(default_factory=dict)
    sizer_breakdown: dict = field(default_factory=dict)
