"""Wave 2 — Comprehensive Fundamental features.

Adds ~55 new ratios on top of the original 10 (FEATURE_COLS_FUND) + 3
composites (FS_COMPOSITE).

Categories:
  - Valuation (12):   peg, ps, pfcf, ev_sales, ev_fcf, ev_ebit,
                      earnings_yield, dividend_yield, fcf_yield,
                      shiller_pe_ttm, p_tangible_bv, buyback_yield
  - Quality (12):     roic, roce, op_margin, net_margin, ebitda_margin,
                      asset_turnover, inv_turnover, recv_turnover,
                      ccc_days, earnings_quality, accruals_ratio,
                      rnd_intensity
  - Growth (10):      rev_3y_cagr, rev_5y_cagr, eps_3y_cagr, eps_5y_cagr,
                      bv_3y_cagr, fcf_3y_cagr, div_3y_cagr, rev_qoq,
                      rev_accel, sgr (sustainable growth rate)
  - Leverage (8):     net_debt_ebitda, interest_coverage, quick_ratio,
                      cash_total_debt, lt_debt_capital, fcf_total_debt,
                      goodwill_assets, intangibles_assets
  - Cash Flow (7):    fcf_abs, fcf_margin, capex_sales, capex_dep,
                      delta_wc, cash_conv, owner_earnings_proxy
  - Composites (6):   magic_formula_rank, qmj_score, ohlson_o,
                      sloan_accruals, mohanram_g, ncav_graham
"""
from __future__ import annotations

from datetime import date as DateType, timedelta
from typing import Optional

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _latest_as_of(panel: pd.DataFrame, concept: str, as_of: DateType,
                   kind: str = "Q") -> Optional[float]:
    sub = panel[(panel["concept"] == concept) & (panel["period_kind"] == kind)]
    if sub.empty:
        return None
    eligible = sub[sub["as_of_ts"].dt.date <= as_of]
    if eligible.empty:
        return None
    return float(eligible.sort_values("as_of_ts").iloc[-1]["value"])


def _ttm_sum(panel: pd.DataFrame, concept: str, as_of: DateType) -> Optional[float]:
    """Sum of last 4 quarterly values."""
    sub = panel[(panel["concept"] == concept) & (panel["period_kind"] == "Q")]
    if sub.empty:
        return None
    eligible = sub[sub["as_of_ts"].dt.date <= as_of].sort_values("period_end")
    if len(eligible) < 4:
        return None
    last4 = eligible.tail(4)
    return float(last4["value"].sum())


def _nth_ago(panel: pd.DataFrame, concept: str, as_of: DateType,
              years_ago: int) -> Optional[float]:
    """Return concept value n years before as_of (annual kind preferred)."""
    target = DateType(as_of.year - years_ago, as_of.month, max(1, as_of.day - 1))
    sub = panel[(panel["concept"] == concept) & (panel["period_kind"] == "A")]
    if sub.empty:
        sub = panel[(panel["concept"] == concept) & (panel["period_kind"] == "Q")]
    if sub.empty:
        return None
    eligible = sub[sub["as_of_ts"].dt.date <= target]
    if eligible.empty:
        return None
    return float(eligible.sort_values("as_of_ts").iloc[-1]["value"])


def _safe_div(a: Optional[float], b: Optional[float]) -> float:
    if a is None or b is None or b == 0 or not np.isfinite(b):
        return float("nan")
    return a / b


def _cagr(start: Optional[float], end: Optional[float], years: int) -> float:
    if start is None or end is None or start <= 0 or end <= 0 or years <= 0:
        return float("nan")
    return (end / start) ** (1.0 / years) - 1.0


# ──────────────────────────────────────────────────────────────────────
# Valuation features
# ──────────────────────────────────────────────────────────────────────


def _market_cap(panel, as_of, price):
    """Market cap = shares * price.

    Primary: SHARES_OUTSTANDING from balance sheet.
    Fallback: NET_INCOME / EPS_DILUTED (or EPS_BASIC) — gives implied shares
    when balance-sheet field missing from XBRL (common for many filers).
    """
    if price is None or np.isnan(price):
        return None
    shares = _latest_as_of(panel, "SHARES_OUTSTANDING", as_of)
    if shares is None or shares <= 0:
        # Fallback via NI / EPS
        ni = _latest_as_of(panel, "NET_INCOME", as_of)
        eps = (_latest_as_of(panel, "EPS_DILUTED", as_of)
                or _latest_as_of(panel, "EPS_BASIC", as_of))
        if ni is not None and eps is not None and abs(eps) > 1e-6:
            shares = ni / eps
        else:
            return None
    if shares is None or shares <= 0:
        return None
    return shares * price


def _enterprise_value(panel, as_of, price):
    mc = _market_cap(panel, as_of, price)
    if mc is None:
        return None
    debt_lt = _latest_as_of(panel, "LONG_TERM_DEBT", as_of) or 0.0
    debt_st = _latest_as_of(panel, "SHORT_TERM_DEBT", as_of) or 0.0
    cash = _latest_as_of(panel, "CASH", as_of) or 0.0
    minority = _latest_as_of(panel, "MINORITY_INTEREST", as_of) or 0.0
    preferred = _latest_as_of(panel, "PREFERRED_STOCK", as_of) or 0.0
    return mc + debt_lt + debt_st - cash + minority + preferred


def compute_valuation_v2(panel: pd.DataFrame, dates: pd.DatetimeIndex,
                          market_close: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame(index=dates)
    cols = ["peg", "ps", "pfcf", "ev_sales", "ev_fcf", "ev_ebit",
            "earnings_yield", "dividend_yield", "fcf_yield",
            "shiller_pe_ttm", "p_tangible_bv", "buyback_yield"]
    for c in cols:
        out[c] = np.nan

    for dt in dates:
        d = dt.date() if hasattr(dt, "date") else dt
        price = float(market_close.get(dt)) if dt in market_close.index else np.nan
        if not np.isfinite(price):
            continue

        rev_ttm = _ttm_sum(panel, "REVENUE", d)
        ni_ttm = _ttm_sum(panel, "NET_INCOME", d)
        eps_ttm = _ttm_sum(panel, "EPS_DILUTED", d) or _ttm_sum(panel, "EPS_BASIC", d)
        cfo_ttm = _ttm_sum(panel, "CFO", d)
        capex_ttm = _ttm_sum(panel, "CAPEX", d)
        oi_ttm = _ttm_sum(panel, "OPERATING_INCOME", d)
        ebitda_ttm = _ttm_sum(panel, "EBITDA", d) or (
            None if oi_ttm is None or _ttm_sum(panel, "DEPRECIATION_AMORT", d) is None
            else oi_ttm + (_ttm_sum(panel, "DEPRECIATION_AMORT", d) or 0)
        )
        div_ttm = _ttm_sum(panel, "DIVIDENDS_PAID", d)
        buyback_ttm = _ttm_sum(panel, "STOCK_BUYBACK", d)
        equity = _latest_as_of(panel, "TOTAL_EQUITY", d)
        goodwill = _latest_as_of(panel, "GOODWILL", d) or 0.0
        intangibles = _latest_as_of(panel, "INTANGIBLES", d) or 0.0
        mc = _market_cap(panel, d, price)
        ev = _enterprise_value(panel, d, price)
        fcf_ttm = (
            None if cfo_ttm is None
            else cfo_ttm - abs(capex_ttm) if capex_ttm is not None else cfo_ttm
        )

        # PEG = P/E ÷ EPS growth %
        eps_yoy = None
        eps_1y = _nth_ago(panel, "EPS_DILUTED", d, 1) or _nth_ago(panel, "EPS_BASIC", d, 1)
        if eps_ttm and eps_1y and eps_1y > 0:
            eps_yoy = (eps_ttm - eps_1y) / abs(eps_1y) * 100.0
        pe = price / eps_ttm if eps_ttm and eps_ttm > 0 else None
        if pe and eps_yoy and eps_yoy > 0:
            out.at[dt, "peg"] = pe / eps_yoy

        out.at[dt, "ps"] = _safe_div(mc, rev_ttm)
        out.at[dt, "pfcf"] = _safe_div(mc, fcf_ttm) if fcf_ttm and fcf_ttm > 0 else np.nan
        out.at[dt, "ev_sales"] = _safe_div(ev, rev_ttm)
        out.at[dt, "ev_fcf"] = _safe_div(ev, fcf_ttm) if fcf_ttm and fcf_ttm > 0 else np.nan
        out.at[dt, "ev_ebit"] = _safe_div(ev, oi_ttm) if oi_ttm and oi_ttm > 0 else np.nan
        out.at[dt, "earnings_yield"] = _safe_div(eps_ttm, price) if eps_ttm else np.nan
        out.at[dt, "dividend_yield"] = _safe_div(abs(div_ttm or 0), mc) if mc else np.nan
        out.at[dt, "fcf_yield"] = _safe_div(fcf_ttm, mc) if fcf_ttm else np.nan
        # Shiller PE proxy — 4Y avg EPS (lighter version of 10Y CAPE)
        eps_avg = []
        for yr in range(0, 4):
            e = _nth_ago(panel, "EPS_DILUTED", d, yr) or _nth_ago(panel, "EPS_BASIC", d, yr)
            if e is not None:
                eps_avg.append(e)
        if eps_avg and price > 0:
            avg = sum(eps_avg) / len(eps_avg)
            if avg > 0:
                out.at[dt, "shiller_pe_ttm"] = price / avg
        # Price to Tangible BV
        if equity is not None:
            tangible_bv = equity - goodwill - intangibles
            if tangible_bv > 0 and mc:
                out.at[dt, "p_tangible_bv"] = mc / tangible_bv
        out.at[dt, "buyback_yield"] = _safe_div(abs(buyback_ttm or 0), mc) if mc else np.nan

    return out


# ──────────────────────────────────────────────────────────────────────
# Quality features
# ──────────────────────────────────────────────────────────────────────


def compute_quality_v2(panel: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    out = pd.DataFrame(index=dates)
    cols = ["roic", "roce", "op_margin", "net_margin", "ebitda_margin",
            "asset_turnover", "inv_turnover", "recv_turnover",
            "ccc_days", "earnings_quality", "accruals_ratio",
            "rnd_intensity"]
    for c in cols:
        out[c] = np.nan

    for dt in dates:
        d = dt.date() if hasattr(dt, "date") else dt
        rev_ttm = _ttm_sum(panel, "REVENUE", d)
        cogs_ttm = _ttm_sum(panel, "COGS", d)
        oi_ttm = _ttm_sum(panel, "OPERATING_INCOME", d)
        ni_ttm = _ttm_sum(panel, "NET_INCOME", d)
        cfo_ttm = _ttm_sum(panel, "CFO", d)
        da_ttm = _ttm_sum(panel, "DEPRECIATION_AMORT", d)
        rnd_ttm = _ttm_sum(panel, "RND_EXPENSE", d)
        tax_ttm = _ttm_sum(panel, "TAX_EXPENSE", d)
        int_ttm = _ttm_sum(panel, "INTEREST_EXPENSE", d)
        assets = _latest_as_of(panel, "TOTAL_ASSETS", d)
        equity = _latest_as_of(panel, "TOTAL_EQUITY", d)
        debt_lt = _latest_as_of(panel, "LONG_TERM_DEBT", d) or 0.0
        debt_st = _latest_as_of(panel, "SHORT_TERM_DEBT", d) or 0.0
        inv = _latest_as_of(panel, "INVENTORY", d)
        recv = _latest_as_of(panel, "RECEIVABLES", d)
        pay = _latest_as_of(panel, "PAYABLES", d)
        cur_l = _latest_as_of(panel, "CURRENT_LIABILITIES", d)
        cur_a = _latest_as_of(panel, "CURRENT_ASSETS", d)

        # ROIC = NOPAT / (Equity + LT Debt + ST Debt)
        if ni_ttm is not None and equity is not None:
            invested = equity + debt_lt + debt_st
            if invested > 0:
                tax_rate = (tax_ttm / (ni_ttm + (tax_ttm or 0))) if (ni_ttm and tax_ttm) else 0.21
                tax_rate = min(0.4, max(0.0, tax_rate))
                nopat = oi_ttm * (1 - tax_rate) if oi_ttm is not None else ni_ttm
                out.at[dt, "roic"] = nopat / invested
                out.at[dt, "roce"] = _safe_div(oi_ttm, invested)

        out.at[dt, "op_margin"] = _safe_div(oi_ttm, rev_ttm)
        out.at[dt, "net_margin"] = _safe_div(ni_ttm, rev_ttm)
        ebitda_ttm = (oi_ttm or 0) + (da_ttm or 0) if (oi_ttm and da_ttm) else None
        out.at[dt, "ebitda_margin"] = _safe_div(ebitda_ttm, rev_ttm)
        out.at[dt, "asset_turnover"] = _safe_div(rev_ttm, assets)
        out.at[dt, "inv_turnover"] = _safe_div(cogs_ttm, inv) if inv and inv > 0 else np.nan
        out.at[dt, "recv_turnover"] = _safe_div(rev_ttm, recv) if recv and recv > 0 else np.nan

        # Cash Conversion Cycle = DIO + DSO - DPO
        if inv and cogs_ttm and recv and rev_ttm and pay:
            dio = 365 * inv / cogs_ttm if cogs_ttm > 0 else 0
            dso = 365 * recv / rev_ttm if rev_ttm > 0 else 0
            dpo = 365 * pay / cogs_ttm if cogs_ttm > 0 else 0
            out.at[dt, "ccc_days"] = dio + dso - dpo

        # Earnings Quality = CFO / NI
        out.at[dt, "earnings_quality"] = _safe_div(cfo_ttm, ni_ttm) if ni_ttm else np.nan

        # Accruals Ratio (Sloan): (NI - CFO) / TotalAssets
        if ni_ttm is not None and cfo_ttm is not None and assets and assets > 0:
            out.at[dt, "accruals_ratio"] = (ni_ttm - cfo_ttm) / assets

        out.at[dt, "rnd_intensity"] = _safe_div(rnd_ttm, rev_ttm)

    return out


# ──────────────────────────────────────────────────────────────────────
# Growth features
# ──────────────────────────────────────────────────────────────────────


def compute_growth_v2(panel: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    out = pd.DataFrame(index=dates)
    cols = ["rev_3y_cagr", "rev_5y_cagr", "eps_3y_cagr", "eps_5y_cagr",
            "bv_3y_cagr", "fcf_3y_cagr", "div_3y_cagr",
            "rev_qoq", "rev_accel", "sgr"]
    for c in cols:
        out[c] = np.nan

    for dt in dates:
        d = dt.date() if hasattr(dt, "date") else dt
        rev_now = _ttm_sum(panel, "REVENUE", d)
        rev_3y = _nth_ago(panel, "REVENUE", d, 3)
        rev_5y = _nth_ago(panel, "REVENUE", d, 5)
        eps_now = _ttm_sum(panel, "EPS_DILUTED", d) or _ttm_sum(panel, "EPS_BASIC", d)
        eps_3y = _nth_ago(panel, "EPS_DILUTED", d, 3) or _nth_ago(panel, "EPS_BASIC", d, 3)
        eps_5y = _nth_ago(panel, "EPS_DILUTED", d, 5) or _nth_ago(panel, "EPS_BASIC", d, 5)
        eq_now = _latest_as_of(panel, "TOTAL_EQUITY", d)
        eq_3y = _nth_ago(panel, "TOTAL_EQUITY", d, 3)
        fcf_now = _ttm_sum(panel, "FREE_CASH_FLOW", d)
        fcf_3y = _nth_ago(panel, "FREE_CASH_FLOW", d, 3)
        div_now = _ttm_sum(panel, "DIVIDENDS_PAID", d)
        div_3y = _nth_ago(panel, "DIVIDENDS_PAID", d, 3)

        out.at[dt, "rev_3y_cagr"] = _cagr(rev_3y, rev_now, 3)
        out.at[dt, "rev_5y_cagr"] = _cagr(rev_5y, rev_now, 5)
        out.at[dt, "eps_3y_cagr"] = _cagr(eps_3y, eps_now, 3)
        out.at[dt, "eps_5y_cagr"] = _cagr(eps_5y, eps_now, 5)
        out.at[dt, "bv_3y_cagr"] = _cagr(eq_3y, eq_now, 3)
        out.at[dt, "fcf_3y_cagr"] = _cagr(fcf_3y, fcf_now, 3) if fcf_now and fcf_3y and fcf_3y > 0 else np.nan
        out.at[dt, "div_3y_cagr"] = _cagr(abs(div_3y or 0), abs(div_now or 0), 3) if div_now and div_3y else np.nan

        # Rev QoQ + acceleration (latest Q vs prev Q vs prev-prev Q)
        sub = panel[(panel["concept"] == "REVENUE") & (panel["period_kind"] == "Q")]
        eligible = sub[sub["as_of_ts"].dt.date <= d].sort_values("period_end")
        if len(eligible) >= 3:
            v_now, v_prev, v_pp = eligible.tail(3)["value"].tolist()
            if v_prev and v_prev != 0:
                qoq = (v_now - v_prev) / abs(v_prev)
                out.at[dt, "rev_qoq"] = qoq
            if v_prev and v_pp and v_prev != 0 and v_pp != 0:
                qoq_prev = (v_prev - v_pp) / abs(v_pp)
                if "rev_qoq" in out.columns:
                    val = out.at[dt, "rev_qoq"]
                    if np.isfinite(val):
                        out.at[dt, "rev_accel"] = val - qoq_prev

        # Sustainable Growth Rate = ROE × (1 - payout ratio)
        roe = _safe_div(_ttm_sum(panel, "NET_INCOME", d), eq_now)
        if np.isfinite(roe) and div_now is not None and eps_now and eps_now != 0:
            ni_ttm = _ttm_sum(panel, "NET_INCOME", d)
            if ni_ttm and ni_ttm != 0:
                payout = abs(div_now) / abs(ni_ttm)
                out.at[dt, "sgr"] = roe * (1 - min(1.0, payout))

    return out


# ──────────────────────────────────────────────────────────────────────
# Leverage features
# ──────────────────────────────────────────────────────────────────────


def compute_leverage_v2(panel: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    out = pd.DataFrame(index=dates)
    cols = ["net_debt_ebitda", "interest_coverage", "quick_ratio",
            "cash_total_debt", "lt_debt_capital", "fcf_total_debt",
            "goodwill_assets", "intangibles_assets"]
    for c in cols:
        out[c] = np.nan

    for dt in dates:
        d = dt.date() if hasattr(dt, "date") else dt
        debt_lt = _latest_as_of(panel, "LONG_TERM_DEBT", d) or 0.0
        debt_st = _latest_as_of(panel, "SHORT_TERM_DEBT", d) or 0.0
        cash = _latest_as_of(panel, "CASH", d) or 0.0
        equity = _latest_as_of(panel, "TOTAL_EQUITY", d)
        assets = _latest_as_of(panel, "TOTAL_ASSETS", d)
        inv = _latest_as_of(panel, "INVENTORY", d) or 0.0
        cur_a = _latest_as_of(panel, "CURRENT_ASSETS", d)
        cur_l = _latest_as_of(panel, "CURRENT_LIABILITIES", d)
        oi_ttm = _ttm_sum(panel, "OPERATING_INCOME", d)
        da_ttm = _ttm_sum(panel, "DEPRECIATION_AMORT", d)
        int_ttm = _ttm_sum(panel, "INTEREST_EXPENSE", d)
        fcf_ttm = _ttm_sum(panel, "FREE_CASH_FLOW", d)
        goodwill = _latest_as_of(panel, "GOODWILL", d) or 0.0
        intangibles = _latest_as_of(panel, "INTANGIBLES", d) or 0.0

        total_debt = debt_lt + debt_st
        net_debt = total_debt - cash
        ebitda = (oi_ttm or 0) + (da_ttm or 0) if (oi_ttm is not None or da_ttm is not None) else None

        if ebitda and ebitda > 0:
            out.at[dt, "net_debt_ebitda"] = net_debt / ebitda
        if int_ttm and int_ttm > 0:
            out.at[dt, "interest_coverage"] = _safe_div(oi_ttm, int_ttm)
        if cur_l and cur_l > 0 and cur_a is not None:
            out.at[dt, "quick_ratio"] = (cur_a - inv) / cur_l
        if total_debt > 0:
            out.at[dt, "cash_total_debt"] = cash / total_debt
            out.at[dt, "fcf_total_debt"] = _safe_div(fcf_ttm, total_debt)
        if equity and (equity + debt_lt) > 0:
            out.at[dt, "lt_debt_capital"] = debt_lt / (equity + debt_lt)
        if assets and assets > 0:
            out.at[dt, "goodwill_assets"] = goodwill / assets
            out.at[dt, "intangibles_assets"] = intangibles / assets

    return out


# ──────────────────────────────────────────────────────────────────────
# Cash flow features
# ──────────────────────────────────────────────────────────────────────


def compute_cashflow_v2(panel: pd.DataFrame, dates: pd.DatetimeIndex,
                         market_close: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame(index=dates)
    cols = ["fcf_abs_log", "fcf_margin", "capex_sales", "capex_dep",
            "delta_wc_assets", "cash_conv", "owner_earnings_yield"]
    for c in cols:
        out[c] = np.nan

    for dt in dates:
        d = dt.date() if hasattr(dt, "date") else dt
        cfo_ttm = _ttm_sum(panel, "CFO", d)
        capex_ttm = _ttm_sum(panel, "CAPEX", d)
        da_ttm = _ttm_sum(panel, "DEPRECIATION_AMORT", d)
        rev_ttm = _ttm_sum(panel, "REVENUE", d)
        ni_ttm = _ttm_sum(panel, "NET_INCOME", d)
        assets = _latest_as_of(panel, "TOTAL_ASSETS", d)
        recv = _latest_as_of(panel, "RECEIVABLES", d)
        recv_prev = _nth_ago(panel, "RECEIVABLES", d, 1)
        inv = _latest_as_of(panel, "INVENTORY", d)
        inv_prev = _nth_ago(panel, "INVENTORY", d, 1)
        pay = _latest_as_of(panel, "PAYABLES", d)
        pay_prev = _nth_ago(panel, "PAYABLES", d, 1)
        price = float(market_close.get(dt)) if dt in market_close.index else np.nan

        fcf_ttm = (
            None if cfo_ttm is None
            else cfo_ttm - abs(capex_ttm) if capex_ttm is not None else cfo_ttm
        )
        if fcf_ttm is not None:
            out.at[dt, "fcf_abs_log"] = float(np.log1p(abs(fcf_ttm)) * np.sign(fcf_ttm))
            out.at[dt, "fcf_margin"] = _safe_div(fcf_ttm, rev_ttm)

        out.at[dt, "capex_sales"] = _safe_div(abs(capex_ttm or 0), rev_ttm)
        out.at[dt, "capex_dep"] = _safe_div(abs(capex_ttm or 0), da_ttm) if da_ttm and da_ttm > 0 else np.nan

        if recv and recv_prev and inv and inv_prev and pay and pay_prev and assets and assets > 0:
            delta_wc = (recv - recv_prev) + (inv - inv_prev) - (pay - pay_prev)
            out.at[dt, "delta_wc_assets"] = delta_wc / assets

        out.at[dt, "cash_conv"] = _safe_div(cfo_ttm, ni_ttm)

        # Owner Earnings (Buffett) = NI + D&A - Maintenance Capex (approx = Capex)
        if ni_ttm is not None and price and not np.isnan(price):
            oe = ni_ttm + (da_ttm or 0) - abs(capex_ttm or 0)
            shares = _latest_as_of(panel, "SHARES_OUTSTANDING", d)
            if shares and shares > 0:
                out.at[dt, "owner_earnings_yield"] = (oe / shares) / price

    return out


# ──────────────────────────────────────────────────────────────────────
# Composite scores
# ──────────────────────────────────────────────────────────────────────


def compute_composites_v2(panel: pd.DataFrame, dates: pd.DatetimeIndex,
                           market_close: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame(index=dates)
    cols = ["magic_formula_score", "qmj_score", "ohlson_o",
            "sloan_accruals_signal", "mohanram_g_score", "ncav_to_mcap"]
    for c in cols:
        out[c] = np.nan

    for dt in dates:
        d = dt.date() if hasattr(dt, "date") else dt
        oi_ttm = _ttm_sum(panel, "OPERATING_INCOME", d)
        ni_ttm = _ttm_sum(panel, "NET_INCOME", d)
        cfo_ttm = _ttm_sum(panel, "CFO", d)
        rev_ttm = _ttm_sum(panel, "REVENUE", d)
        assets = _latest_as_of(panel, "TOTAL_ASSETS", d)
        equity = _latest_as_of(panel, "TOTAL_EQUITY", d)
        cash = _latest_as_of(panel, "CASH", d) or 0.0
        recv = _latest_as_of(panel, "RECEIVABLES", d) or 0.0
        inv = _latest_as_of(panel, "INVENTORY", d) or 0.0
        cur_l = _latest_as_of(panel, "CURRENT_LIABILITIES", d) or 0.0
        tot_l = _latest_as_of(panel, "TOTAL_LIABILITIES", d) or 0.0
        debt_lt = _latest_as_of(panel, "LONG_TERM_DEBT", d) or 0.0
        debt_st = _latest_as_of(panel, "SHORT_TERM_DEBT", d) or 0.0
        ppe = _latest_as_of(panel, "PROPERTY_PLANT_EQ", d) or 0.0
        re_acc = _latest_as_of(panel, "RETAINED_EARNINGS", d) or 0.0
        price = float(market_close.get(dt)) if dt in market_close.index else np.nan

        # Magic Formula (Greenblatt) — rank-friendly: EBIT/EV + EBIT/(NWC+NetFA)
        if oi_ttm and price and np.isfinite(price):
            mc = _market_cap(panel, d, price)
            ev = _enterprise_value(panel, d, price)
            if ev and ev > 0 and mc and mc > 0:
                ebit_ev = oi_ttm / ev
                roc_denom = (assets or 0) - cur_l + 1e-9
                ebit_roc = oi_ttm / roc_denom if roc_denom > 0 else 0
                out.at[dt, "magic_formula_score"] = ebit_ev + ebit_roc * 0.5

        # QMJ (Quality Minus Junk, Asness/Frazzini/Pedersen 단순화):
        #   profitability + safety + growth z-scores. 여기는 raw composite.
        roe = _safe_div(ni_ttm, equity)
        gp_assets = _safe_div(_ttm_sum(panel, "GROSS_PROFIT", d), assets)
        margin = _safe_div(oi_ttm, rev_ttm)
        leverage = _safe_div(tot_l, assets)
        if np.isfinite(roe) or np.isfinite(gp_assets) or np.isfinite(margin):
            qmj = ((roe if np.isfinite(roe) else 0) * 0.4
                   + (gp_assets if np.isfinite(gp_assets) else 0) * 0.3
                   + (margin if np.isfinite(margin) else 0) * 0.3
                   - (leverage if np.isfinite(leverage) else 0) * 0.2)
            out.at[dt, "qmj_score"] = qmj

        # Ohlson O-Score (1980 bankruptcy)
        # Simplified to use available concepts; classical: -1.32 - 0.407 log(TA/GNP) + 6.03 TLTA - ...
        if assets and assets > 0 and ni_ttm is not None:
            tlta = (tot_l) / assets
            nita = ni_ttm / assets
            futl = cfo_ttm / tot_l if tot_l > 0 and cfo_ttm else 0
            chin = ni_ttm / (abs(ni_ttm) + 1e-9)   # sign indicator
            out.at[dt, "ohlson_o"] = -1.32 + 6.03 * tlta - 1.43 * nita - 2.37 * futl - 0.521 * chin

        # Sloan Accruals signal — high accruals = bad earnings quality
        if assets and assets > 0 and ni_ttm is not None and cfo_ttm is not None:
            out.at[dt, "sloan_accruals_signal"] = -(ni_ttm - cfo_ttm) / assets   # higher = better

        # Mohanram G-Score (growth-firm quality, 8 binary signals, simplified)
        g_score = 0
        if roe and np.isfinite(roe) and roe > 0.1: g_score += 1
        if cfo_ttm and assets and cfo_ttm / assets > 0.05: g_score += 1
        if cfo_ttm and ni_ttm and cfo_ttm > ni_ttm: g_score += 1
        rev_prev = _nth_ago(panel, "REVENUE", d, 1)
        if rev_ttm and rev_prev and rev_prev > 0 and rev_ttm / rev_prev > 1.1: g_score += 1
        if _ttm_sum(panel, "RND_EXPENSE", d) and rev_ttm:
            if _ttm_sum(panel, "RND_EXPENSE", d) / rev_ttm > 0.03: g_score += 1
        if oi_ttm and rev_ttm and (oi_ttm / rev_ttm) > 0.05: g_score += 1
        if debt_lt and equity and (debt_lt / equity) < 0.5: g_score += 1
        if cash and cur_l and cash > cur_l: g_score += 1
        out.at[dt, "mohanram_g_score"] = float(g_score)

        # NCAV (Graham) = Current Assets - Total Liabilities; ratio to market cap
        cur_a = _latest_as_of(panel, "CURRENT_ASSETS", d) or 0.0
        ncav = cur_a - tot_l
        if price and np.isfinite(price):
            mc = _market_cap(panel, d, price)
            if mc and mc > 0:
                out.at[dt, "ncav_to_mcap"] = ncav / mc

    return out


# ──────────────────────────────────────────────────────────────────────
# Top-level
# ──────────────────────────────────────────────────────────────────────


def compute_fundamental_v2(panel: pd.DataFrame, dates: pd.DatetimeIndex,
                            market_close: pd.Series) -> pd.DataFrame:
    parts = [
        compute_valuation_v2(panel, dates, market_close),
        compute_quality_v2(panel, dates),
        compute_growth_v2(panel, dates),
        compute_leverage_v2(panel, dates),
        compute_cashflow_v2(panel, dates, market_close),
        compute_composites_v2(panel, dates, market_close),
    ]
    return pd.concat(parts, axis=1)
