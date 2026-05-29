"""US equity transaction costs.

US has no per-trade transaction tax. The only mandatory fees are:

- **SEC Section 31 fee** — on SELL of covered equity securities;
  rate changes annually around mid-year. As of fiscal year 2025 the
  rate is **$27.80 per $1,000,000** of sale proceeds (≈ 0.278 bps).
  Verify yearly at https://www.sec.gov/files/feerate-2025.pdf
- **FINRA Trading Activity Fee (TAF)** — on SELL of covered
  securities, **$0.000166 per share** (capped at $8.30 per trade),
  effective 2024-01-08. https://www.finra.org/rules-guidance/key-topics/trading-activity-fee
- **Broker commission** — many retail brokers (Alpaca, IBKR Lite,
  Schwab, Fidelity) charge $0 for US equities. Default 0.

For high-precision live trading we will pull SEC/FINRA rate updates
once a year (Phase 6). For Phase 0 (paper trading) the above
constants are accurate enough to keep simulated P&L honest.

We do NOT model US capital gains tax here — it's a personal
year-end matter, not a per-trade cost.
"""
from __future__ import annotations

from markets.base import TaxBreakdown

# 2025 rates
SEC_FEE_PER_USD = 27.80 / 1_000_000          # $27.80 per $1,000,000 of sale
FINRA_TAF_PER_SHARE = 0.000166
FINRA_TAF_CAP = 8.30
DEFAULT_COMMISSION_PER_SHARE = 0.0


def compute_us_tax(
    *,
    side: str,
    gross_value: float,
    commission_bps: float | None = None,
    shares: int | None = None,
    price_per_share: float | None = None,
) -> TaxBreakdown:
    """Compute US-side per-trade costs in USD.

    Parameters
    ----------
    side : 'BUY' | 'SELL'
    gross_value : float
        Trade notional in USD.
    commission_bps : float, optional
        If provided, used to compute commission as gross_value * bps / 10_000.
        If omitted, commission is computed per-share when ``shares`` is
        provided; otherwise commission is zero (typical for retail $0
        brokers).
    shares : int, optional
        Share count, needed for the FINRA TAF.
    price_per_share : float, optional
        Used only when neither ``commission_bps`` nor ``shares`` is provided —
        not yet needed but kept in signature for future broker-tier overrides.
    """
    if gross_value < 0:
        raise ValueError("gross_value must be non-negative")
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")

    # Commission
    if commission_bps is not None:
        commission = gross_value * commission_bps / 10_000
    elif shares is not None:
        commission = shares * DEFAULT_COMMISSION_PER_SHARE
    else:
        commission = 0.0

    # SEC fee on SELL only
    sec_fee = gross_value * SEC_FEE_PER_USD if side == "SELL" else 0.0

    # FINRA TAF on SELL only, capped
    if side == "SELL" and shares is not None and shares > 0:
        taf = min(shares * FINRA_TAF_PER_SHARE, FINRA_TAF_CAP)
    else:
        taf = 0.0

    return TaxBreakdown(
        commission=round(commission, 4),
        transaction_tax=round(sec_fee + taf, 4),  # bundled as "tax" for accounting symmetry with KR
        other_fees=0.0,
    )
