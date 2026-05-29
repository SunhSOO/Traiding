"""Position sizing.

Three-step compute:

1. **Base fraction** of account equity, scaled by composite magnitude
   and confidence:
       base_pct = base_position_fraction
                * (|composite| / 100)        # in [0, 1]
                * composite_confidence

2. **Volatility target adjustment**: scale up/down so the expected
   per-position daily volatility (in % of equity) equals
   ``target_volatility_bps``. Recent ATR is the volatility estimate.

3. **Hard cap**: cap at ``max_position_fraction`` of equity.

Result is a USD/KRW notional that the caller divides by current
price to get share count (then rounds per ticker's lot rule).

Sizer NEVER opens shorts on its own — it returns the magnitude of
the position. The runner decides side from the composite sign.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from decision.types import DecisionConfig


@dataclass(frozen=True)
class SizerInputs:
    """All inputs the sizer needs. Currency-aware via ``account_currency``."""

    account_equity: float
    account_currency: str            # 'KRW' | 'USD'
    current_price: float             # in ticker's native ccy
    ticker_currency: str             # 'KRW' | 'USD'
    composite_score: float           # signed -100..+100
    composite_confidence: float      # 0..1
    recent_atr: Optional[float] = None
    fx_to_account: float = 1.0       # multiply ticker_ccy → account_ccy


@dataclass(frozen=True)
class SizerOutput:
    """Output of :func:`size_position`. ``shares`` is the final integer
    target; ``notional_account_ccy`` is what that maps to in the
    account base currency (after FX). Both 0 when sizer abstains."""

    shares: int
    notional_account_ccy: float
    base_fraction: float
    vol_adjusted_fraction: float
    capped_fraction: float
    breakdown: dict = field(default_factory=dict)


def size_position(
    inputs: SizerInputs,
    *,
    config: Optional[DecisionConfig] = None,
) -> SizerOutput:
    """Compute target position size for one ticker.

    Returns 0-sized output when inputs are degenerate (zero price,
    zero composite, zero confidence) so the runner can still emit a
    HOLD audit row without special-casing exceptions."""
    config = config or DecisionConfig()
    breakdown: dict = {}

    if inputs.current_price <= 0 or inputs.account_equity <= 0:
        return _zero_output(
            "non_positive_inputs",
            {"price": inputs.current_price, "equity": inputs.account_equity},
        )

    magnitude = abs(inputs.composite_score) / 100.0
    base = config.base_position_fraction * magnitude * inputs.composite_confidence
    breakdown["magnitude"] = magnitude
    breakdown["base_fraction"] = base

    if base <= 0:
        return _zero_output("zero_base_fraction", breakdown)

    # Volatility target adjustment
    vol_adjusted = base
    if inputs.recent_atr is not None and inputs.recent_atr > 0 and inputs.current_price > 0:
        per_share_vol_bps = (inputs.recent_atr / inputs.current_price) * 10_000
        if per_share_vol_bps > 0:
            # Scale = target_vol / observed_vol. Cap the up-scale at 3×
            # so a tiny-ATR ticker doesn't blow position sizes up.
            scale = min(3.0, config.target_volatility_bps / per_share_vol_bps)
            vol_adjusted = base * scale
            breakdown["per_share_vol_bps"] = per_share_vol_bps
            breakdown["vol_scale"] = scale
    breakdown["vol_adjusted_fraction"] = vol_adjusted

    # Hard cap
    capped = min(vol_adjusted, config.max_position_fraction)
    breakdown["capped_fraction"] = capped

    # Notional in account currency
    notional_account_ccy = capped * inputs.account_equity
    # Convert to ticker-native price → share count
    notional_ticker_ccy = notional_account_ccy / inputs.fx_to_account
    shares_float = notional_ticker_ccy / inputs.current_price
    shares = int(shares_float)         # round down — never over-allocate

    if shares <= 0:
        return _zero_output("rounded_to_zero_shares",
                            {**breakdown, "shares_float": shares_float})

    return SizerOutput(
        shares=shares,
        notional_account_ccy=shares * inputs.current_price * inputs.fx_to_account,
        base_fraction=base,
        vol_adjusted_fraction=vol_adjusted,
        capped_fraction=capped,
        breakdown=breakdown,
    )


def _zero_output(reason: str, breakdown: dict) -> SizerOutput:
    return SizerOutput(
        shares=0, notional_account_ccy=0.0,
        base_fraction=0.0, vol_adjusted_fraction=0.0, capped_fraction=0.0,
        breakdown={"abstain_reason": reason, **breakdown},
    )
