"""Korean equity transaction costs.

As of 2025, the relevant components for KOSPI / KOSDAQ trades are:

| Component | KOSPI | KOSDAQ | Applies to |
|-----------|-------|--------|------------|
| 증권거래세 (securities tx tax) | 0.00 % | 0.15 % | SELL only (KOSPI's portion was abolished 2024) |
| 농어촌특별세 (agri/fishery surtax) | 0.15 % | 0.00 % | SELL on KOSPI only |
| Effective transaction tax on SELL | 0.15 % | 0.15 % | SELL only |
| Commission (broker) | varies | varies | BUY and SELL (default we use: 1.5 bps) |
| 유관기관수수료 (clearing/exchange) | ~0.36 bps | ~0.36 bps | BUY and SELL |

We DO NOT distinguish KOSPI vs KOSDAQ in the tax math because the
effective combined transaction tax is the same 0.15 % on the sell
side for both. Sources / future references:
- https://www.krx.co.kr/contents/MKD/04/0402/0402010000/MKD04020100000.jsp
- https://www.fss.or.kr/  (정책 발표 시 갱신)

Capital-gains tax on retail-investor stock profits (소액주주) is
currently NOT applied at order time and is settled annually by NTS
based on listing/threshold rules — out of scope for per-trade cost.
For large-shareholder (대주주) status, capital gains apply but that
is a per-investor classification, not a per-trade fee, so it lives
in a separate (Phase 4) calculation.

ALL constants are encoded as bps (1 bps = 0.01 %).
"""
from __future__ import annotations

from markets.base import TaxBreakdown

DEFAULT_COMMISSION_BPS = 1.5     # broker default; user can override
CLEARING_FEE_BPS = 0.36          # 유관기관수수료 (양방향)
SELL_TX_TAX_BPS = 15.0           # 거래세 0.15 % (SELL only, KOSPI+KOSDAQ effective)


def compute_kr_tax(
    *,
    side: str,
    gross_value: float,
    commission_bps: float | None = None,
) -> TaxBreakdown:
    """Compute KR-side per-trade costs in KRW.

    Parameters
    ----------
    side : 'BUY' | 'SELL'
        Direction of the trade. SELL incurs the 0.15 % transaction tax;
        BUY does not.
    gross_value : float
        Notional value of the trade in KRW (price * shares).
    commission_bps : float, optional
        Broker commission in bps. Defaults to ``DEFAULT_COMMISSION_BPS``.

    Returns
    -------
    TaxBreakdown
        Itemised commission / transaction_tax / other_fees in KRW.
    """
    if gross_value < 0:
        raise ValueError("gross_value must be non-negative")
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")

    comm_bps = commission_bps if commission_bps is not None else DEFAULT_COMMISSION_BPS
    commission = gross_value * comm_bps / 10_000
    clearing = gross_value * CLEARING_FEE_BPS / 10_000
    tx_tax = gross_value * SELL_TX_TAX_BPS / 10_000 if side == "SELL" else 0.0

    return TaxBreakdown(
        commission=round(commission, 4),
        transaction_tax=round(tx_tax, 4),
        other_fees=round(clearing, 4),
    )
