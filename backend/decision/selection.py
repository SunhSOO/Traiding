"""SELECTION + MARKET layer of the integrated pipeline (the "WHAT").

Turns the cross-sectional alpha's recommendations into:
  * a MARKET READ  — regime + breadth + conviction → recommended `target_exposure`
                     (the 지수/시황 overlay; D4: basket is our active index, no ETF v1)
  * a SELECTION BASKET — the alpha's intended holdings (top-decile = in_basket),
                     equal target weight (D5), persisted for the execution layer.

Pure-ish: takes the recommender output `recs` (DataFrame from
ProductionRecommender.recommend) + regime, computes the read & basket, and
persists `market_read` / `selection_basket`. The EXECUTION layer reads the
basket and applies per-stock technical timing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as DateType, datetime
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.models.integrated import MarketRead, SelectionBasket

# Recommended overall invested fraction by regime (D4 exposure overlay).
# Covers both the 3-state voting classifier and the 5-state HMM labels.
EXPO_BY_REGIME = {
    "crisis": 0.0, "risk_crisis": 0.0,
    "risk_off": 0.4,
    "neutral": 0.7,
    "risk_on": 1.0, "calm_bull": 1.0,
}


def _base_exposure(regime: Optional[str]) -> float:
    return EXPO_BY_REGIME.get((regime or "neutral").lower(), 0.7)


@dataclass
class BasketName:
    ticker: str
    rank_pct: float
    target_weight: float
    target_price: Optional[float]
    pred_ret_21d: Optional[float]
    band_low: Optional[float]
    band_high: Optional[float]


@dataclass
class SelectionResult:
    market: str
    as_of: DateType
    regime: Optional[str]
    target_exposure: float
    breadth: float
    avg_conviction: float
    basket: list[BasketName] = field(default_factory=list)   # in_basket names only


def _latest_regime(session: Session, market: str) -> tuple[Optional[str], float]:
    row = session.execute(text(
        "SELECT label, confidence FROM market_regime WHERE market=:m ORDER BY ts DESC LIMIT 1"
    ), {"m": market}).first()
    if not row:
        return None, 0.0
    return str(row[0]).lower(), float(row[1] or 0.0)


def run_selection(
    session: Session,
    *,
    market: str,
    as_of: datetime,
    recs: pd.DataFrame,
    feat_df: Optional[pd.DataFrame] = None,
    decile: float = 0.10,
    persist: bool = True,
) -> SelectionResult:
    """Compute the market read + selection basket from recommender output.

    `recs` columns expected: ticker, rank_pct, pred_ret, target_price,
    band_low, band_high, action (BUY for top-decile). `feat_df` (optional) is
    the underlying feature matrix; when it carries ``px_vs_sma200`` we blend a
    calibration-free *price* breadth (fraction above the 200d SMA) with the
    model's predicted breadth, so a mis-scaled q50 level can't swing exposure.
    """
    as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
    regime, regime_conf = _latest_regime(session, market)

    n = max(len(recs), 1)
    # breadth: blend model-predicted breadth (fraction with pred_ret>0) with a
    # price-based breadth (fraction above 200d SMA) — the latter is standard and
    # calibration-free, so it stabilises the exposure overlay.
    pred_breadth = float((pd.to_numeric(recs["pred_ret"], errors="coerce") > 0).mean()) if len(recs) else 0.0
    price_breadth = None
    if feat_df is not None and "px_vs_sma200" in getattr(feat_df, "columns", []):
        pv = pd.to_numeric(feat_df["px_vs_sma200"], errors="coerce").dropna()
        if len(pv):
            price_breadth = float((pv > 0).mean())     # px_vs_sma200 = price/SMA200 − 1
    breadth = pred_breadth if price_breadth is None else 0.5 * (pred_breadth + price_breadth)
    breadth_parts = {"pred_breadth": round(pred_breadth, 4),
                     "price_breadth": (round(price_breadth, 4) if price_breadth is not None else None)}
    in_basket = recs[recs["action"] == "BUY"].copy()
    n_basket = max(len(in_basket), 1)
    # conviction: mean predicted 21d return of the chosen names. (Using
    # |rank_pct-0.5| here would be ~constant — the basket is by definition the
    # top decile — so it must come from the model's magnitude, not the rank.)
    avg_conv = float(pd.to_numeric(in_basket["pred_ret"], errors="coerce").mean()) if len(in_basket) else 0.0

    base = _base_exposure(regime)
    # breadth nudge: only REDUCE below the regime cap when breadth is thin.
    target_exposure = round(base * min(1.0, 0.4 + breadth), 4)
    target_weight = round(1.0 / n_basket, 5)

    result = SelectionResult(
        market=market, as_of=as_of_date, regime=regime,
        target_exposure=target_exposure, breadth=round(breadth, 4),
        avg_conviction=round(avg_conv, 4),
    )
    for _, r in in_basket.iterrows():
        result.basket.append(BasketName(
            ticker=str(r["ticker"]), rank_pct=float(r["rank_pct"]), target_weight=target_weight,
            target_price=_f(r.get("target_price")), pred_ret_21d=_f(r.get("pred_ret")),
            band_low=_f(r.get("band_low")), band_high=_f(r.get("band_high")),
        ))

    if persist:
        _persist(session, market, as_of_date, regime, regime_conf, result, recs,
                 target_weight, breadth_parts)
    return result


def _f(v) -> Optional[float]:
    try:
        return None if v is None or pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return None


def _persist(session, market, as_of_date, regime, regime_conf, result, recs,
             target_weight, breadth_parts=None):
    # idempotent: clear today's rows then re-insert
    session.execute(text("DELETE FROM market_read WHERE market=:m AND as_of=:d"),
                    {"m": market, "d": as_of_date})
    session.execute(text("DELETE FROM selection_basket WHERE market=:m AND as_of=:d"),
                    {"m": market, "d": as_of_date})
    inputs = {"n_universe": int(len(recs)), "n_basket": len(result.basket)}
    if breadth_parts:
        inputs.update(breadth_parts)   # pred_breadth / price_breadth for transparency
    session.add(MarketRead(
        market=market, as_of=as_of_date, regime=(regime or "neutral").upper(),
        regime_conf=regime_conf, breadth=result.breadth, avg_conviction=result.avg_conviction,
        target_exposure=result.target_exposure,
        inputs=inputs,
    ))
    for _, r in recs.iterrows():
        inb = bool(r["action"] == "BUY")
        session.add(SelectionBasket(
            market=market, as_of=as_of_date, ticker=str(r["ticker"]),
            rank_pct=float(r["rank_pct"]),
            target_weight=(target_weight if inb else 0.0),
            pred_ret_21d=_f(r.get("pred_ret")), target_price=_f(r.get("target_price")),
            band_low=_f(r.get("band_low")), band_high=_f(r.get("band_high")),
            in_basket=inb, regime=(regime or "neutral").upper(),
            market_exposure=result.target_exposure,
        ))
