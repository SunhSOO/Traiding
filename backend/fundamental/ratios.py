"""Financial-ratio computation from canonical FinancialFact rows.

We expose pure functions that take a "concepts map" (dict from
canonical concept code → latest value for the period of interest)
and a market price. They return a :class:`Ratios` object.

The concepts map shape matches what
:func:`load_latest_concepts` returns; that helper queries
``financial_facts`` filtered by ``as_of`` for look-ahead safety.

Growth ratios (revenue / NI / etc.) need TWO periods: current and
year-ago. Pass them as separate maps.

Every ratio computation tolerates missing inputs by returning None
instead of raising — many KR small-caps just don't report some line
items. The downstream scorer maps None to "insufficient data" for
that ratio's contribution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Ratios:
    """Computed ratios for one ticker. Any field may be None when the
    underlying concept was missing."""

    per: Optional[float] = None              # Price / Earnings
    pbr: Optional[float] = None              # Price / Book
    roe: Optional[float] = None              # Net income / Equity
    roa: Optional[float] = None              # Net income / Assets
    op_margin: Optional[float] = None        # Op income / Revenue
    net_margin: Optional[float] = None       # Net income / Revenue
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None    # Current assets / Current liabilities
    fcf_yield: Optional[float] = None        # (CFO - |CAPEX|) / Market cap
    revenue_growth_yoy: Optional[float] = None  # decimal, 0.10 = +10%
    earnings_growth_yoy: Optional[float] = None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @property
    def defined_count(self) -> int:
        return sum(1 for k in self.__dataclass_fields__ if getattr(self, k) is not None)


# ──────────────────────────────────────────────────────────────────────


def compute_ratios(
    *,
    current: dict[str, float],
    prior_year: Optional[dict[str, float]] = None,
    price: Optional[float] = None,
    shares_outstanding: Optional[float] = None,
) -> Ratios:
    """Compute all ratios from canonical concepts.

    Parameters
    ----------
    current : dict
        Latest reported values keyed by canonical concept code
        (REVENUE, NET_INCOME, TOTAL_ASSETS, …).
    prior_year : dict, optional
        Same-period-prior-year values for growth ratios.
    price : float, optional
        Latest market price per share (for P/E, P/B, FCF-yield).
    shares_outstanding : float, optional
        For market-cap derivation when SHARES_OUTSTANDING not in `current`.
    """
    shares = current.get("SHARES_OUTSTANDING") or shares_outstanding
    market_cap = _market_cap(price, shares)

    revenue = current.get("REVENUE")
    op_income = current.get("OPERATING_INCOME")
    net_income = current.get("NET_INCOME")
    total_assets = current.get("TOTAL_ASSETS")
    total_equity = current.get("TOTAL_EQUITY")
    total_liab = current.get("TOTAL_LIABILITIES")
    current_assets = current.get("CURRENT_ASSETS")
    current_liab = current.get("CURRENT_LIABILITIES")
    cfo = current.get("CFO")
    capex = current.get("CAPEX")
    eps = current.get("EPS_BASIC")

    return Ratios(
        per=_safe_div(price, eps) if price is not None else None,
        pbr=_safe_div(market_cap, total_equity),
        roe=_safe_div(net_income, total_equity),
        roa=_safe_div(net_income, total_assets),
        op_margin=_safe_div(op_income, revenue),
        net_margin=_safe_div(net_income, revenue),
        debt_to_equity=_safe_div(total_liab, total_equity),
        current_ratio=_safe_div(current_assets, current_liab),
        fcf_yield=_compute_fcf_yield(cfo, capex, market_cap),
        revenue_growth_yoy=_growth(revenue, prior_year.get("REVENUE") if prior_year else None),
        earnings_growth_yoy=_growth(net_income, prior_year.get("NET_INCOME") if prior_year else None),
    )


# ──────────────────────────────────────────────────────────────────────


def _safe_div(num: Optional[float], denom: Optional[float]) -> Optional[float]:
    if num is None or denom is None or denom == 0:
        return None
    return float(num) / float(denom)


def _growth(curr: Optional[float], prior: Optional[float]) -> Optional[float]:
    if curr is None or prior is None or prior == 0:
        return None
    # Use absolute denominator so growth is meaningful when prior was negative.
    return (float(curr) - float(prior)) / abs(float(prior))


def _compute_fcf_yield(
    cfo: Optional[float], capex: Optional[float], market_cap: Optional[float],
) -> Optional[float]:
    if cfo is None or capex is None or market_cap is None or market_cap == 0:
        return None
    # CAPEX from cash-flow statement is usually negative in raw form;
    # |capex| handles both sign conventions.
    fcf = float(cfo) - abs(float(capex))
    return fcf / float(market_cap)


def _market_cap(price: Optional[float], shares: Optional[float]) -> Optional[float]:
    if price is None or shares is None:
        return None
    return float(price) * float(shares)
