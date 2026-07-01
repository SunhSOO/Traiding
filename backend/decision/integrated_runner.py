"""Integrated runner — the selection→execution pipeline (A→D).

  A. MARKET READ + B. SELECTION  (decision/selection.run_selection):
       cross-sectional alpha → basket (WHAT) + target_exposure (시황).
  C. EXECUTION TIMING            (technical per-stock, D1 = timing only):
       for each basket name, the technical score (trend/momentum/mean-rev/
       red-green on daily bars) gates the ENTRY (delay while bearish) and the
       EXIT (sell a held name on clear technical breakdown OR basket drop).
       Technical NEVER vetoes a basket pick — it only times it (D1).
  D. RISK + BROKER + AUDIT       (reuse RiskEngine + PaperBroker):
       size = equity × target_exposure × (1/n_basket); 2-stage audit
       (selection reason + timing reason). Account = core-kr/us (D2).

Defensive: target_exposure≈0 (crisis) ⇒ close all, no new buys.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

from brokers.base import BrokerAdapter, OrderIntent, OrderSide, OrderType
from core.models.audit import DecisionAudit
from core.risk import RiskEngine, RiskLimits
from core.types import Market
from decision.production_inference import ProductionRecommender, SizingConfig
from decision.selection import run_selection, SelectionResult
from technical.runner import score_one_ticker

# Technical timing thresholds (score in [-100,+100]). D1: timing only.
ENTRY_MIN = 0.0     # enter only when technical not bearish; else WAIT (delay)
EXIT_MAX = -30.0    # exit a held name on clear technical breakdown
MIN_EXPOSURE = 0.02  # below this, treat as fully defensive (close all)
# At/above this target_exposure, concentrate the exposure into the timed passers
# (bull → amplify); below it, keep the cash-style per-basket slot (bear → protect).
CONCENTRATE_MIN_EXPOSURE = 0.6


@dataclass
class IntegratedReport:
    market: str
    regime: Optional[str] = None
    target_exposure: float = 1.0
    breadth: float = 0.0
    basket_size: int = 0
    buys_executed: int = 0
    sells_executed: int = 0
    waiting: int = 0          # in basket but technical says wait
    rejected: int = 0
    defensive: bool = False
    concentrated: bool = False  # regime-adaptive merge: concentrate vs cash-style
    errors: list[str] = field(default_factory=list)


def _tech_score(session: Session, market: Market, ticker: str, as_of: datetime) -> Optional[float]:
    ts = score_one_ticker(session, market=market, ticker=ticker, as_of=as_of)
    return None if ts is None else float(ts.score)


def run_integrated_decisions(
    session: Session,
    *,
    market: Market,
    as_of: datetime,
    broker: BrokerAdapter,
    risk_engine: RiskEngine,
    risk_limits: RiskLimits,
    recommender: ProductionRecommender,
    feat_df: pd.DataFrame,
    close_map: dict[str, float],
    cfg: SizingConfig = SizingConfig(),
) -> IntegratedReport:
    rep = IntegratedReport(market=market.value)

    # ── A+B: market read + selection basket (the WHAT) ──
    recs = recommender.recommend(feat_df, close_map, cfg=cfg)
    sel: SelectionResult = run_selection(session, market=market.value, as_of=as_of, recs=recs,
                                         feat_df=feat_df, persist=True)
    rep.regime = sel.regime
    rep.target_exposure = sel.target_exposure
    rep.breadth = sel.breadth
    rep.basket_size = len(sel.basket)
    rep.defensive = sel.target_exposure < MIN_EXPOSURE
    basket = {b.ticker: b for b in sel.basket}

    account = broker.get_account()
    ccy = account.base_currency
    positions = {p.ticker: p for p in broker.get_positions()}
    cash = float(account.balance)
    pos_value = sum((p.current_price or p.entry_price) * p.volume for p in positions.values())
    equity = cash + pos_value
    n_basket = max(len(basket), 1)
    # per-name target notional = equity × exposure overlay × equal weight (D5)
    per_name = equity * sel.target_exposure / n_basket

    def audit(ticker, action, size_value, *, rank_pct=None, pred_ret=None, target_price=None,
              tech=None, timing, exec_result=None, rejected=False):
        snap = {"regime": sel.regime, "target_exposure": sel.target_exposure,
                "stage_selection": {"rank_pct": rank_pct, "pred_ret_21d": pred_ret,
                                    "target_price": target_price, "in_basket": ticker in basket},
                "stage_timing": {"tech_score": tech, "decision": timing}}
        session.add(DecisionAudit(
            market=market.value, ticker=str(ticker), decision_ts=as_of,
            composite_score=(float(round((rank_pct - 0.5) * 200, 3)) if rank_pct is not None else None),
            action=action, size_value=(float(round(size_value, 6)) if size_value else None),
            size_currency=ccy, model_version=f"integrated_{market.value}_v1",
            inputs_snapshot=snap,
            execution_result=(None if exec_result is None else {
                "ok": exec_result.ok, "fill_price": exec_result.fill_price,
                "fill_volume": exec_result.fill_volume, "error": exec_result.error}),
            error=("risk_rejected" if rejected else None),
        ))

    # ── C+D: EXITS — held names that left the basket, broke down technically, or defensive ──
    for ticker, pos in positions.items():
        b = basket.get(ticker)
        tech = _tech_score(session, market, ticker, as_of)
        if rep.defensive:
            reason = "exit_defensive"
        elif b is None:
            reason = "exit_basket_drop"
        elif tech is not None and tech <= EXIT_MAX:
            reason = "exit_tech_breakdown"
        else:
            continue  # keep holding
        intent = OrderIntent(market=market, ticker=ticker, side=OrderSide.SELL,
                             order_type=OrderType.MARKET, volume=pos.volume, comment=reason)
        try:
            ex = broker.execute(intent)
            audit(ticker, "SELL", None, rank_pct=(b.rank_pct if b else None),
                  tech=tech, timing=reason, exec_result=ex)
            if ex.ok:
                rep.sells_executed += 1
        except Exception as e:
            rep.errors.append(f"sell {ticker}: {e}")

    # ── C+D: ENTRIES — basket names not held, gated by technical timing ──
    if not rep.defensive:
        ordered = sorted(basket.values(), key=lambda b: -b.rank_pct)  # strongest conviction first
        # Pass 1 — technical timing gate: collect the passers, audit the waiters.
        passers: list[tuple] = []
        for b in ordered:
            if b.ticker in positions:
                continue  # already long
            tech = _tech_score(session, market, b.ticker, as_of)
            if tech is None or tech < ENTRY_MIN:   # D1: delay entry while bearish (don't veto)
                audit(b.ticker, "WAIT", None, rank_pct=b.rank_pct, pred_ret=b.pred_ret_21d,
                      target_price=b.target_price, tech=tech, timing="wait_tech")
                rep.waiting += 1
                continue
            price = close_map.get(b.ticker)
            if price and price > 0:
                passers.append((b, tech, price))

        # Sizing — regime-adaptive merge (backtest sweep 2026-06-30): a favourable
        # regime (high target_exposure) CONCENTRATES the exposure into the timed
        # passers (bull → amplify); a defensive/low-exposure regime keeps the
        # cash-style per-basket slot so waiters stay cash (bear → protect).
        rep.concentrated = sel.target_exposure >= CONCENTRATE_MIN_EXPOSURE
        n_deploy = max(len(passers), 1) if rep.concentrated else n_basket
        per_deploy = equity * sel.target_exposure / n_deploy

        # Pass 2 — size, risk-check, execute.
        avail = float(broker.get_account().balance)
        for b, tech, price in passers:
            size_value = min(per_deploy, avail)
            if size_value < equity * 0.005:
                continue
            volume = size_value / price
            avail -= size_value
            intent = OrderIntent(market=market, ticker=b.ticker, side=OrderSide.BUY,
                                 order_type=OrderType.MARKET, volume=volume,
                                 tp=b.target_price, comment="integrated_long")
            risk = risk_engine.check(intent, _risk_state(session, broker), risk_limits)
            if not risk.all_passed:
                audit(b.ticker, "REJECTED", size_value, rank_pct=b.rank_pct, pred_ret=b.pred_ret_21d,
                      target_price=b.target_price, tech=tech, timing="enter_ok", rejected=True)
                rep.rejected += 1
                continue
            try:
                ex = broker.execute(intent)
                audit(b.ticker, "BUY", size_value, rank_pct=b.rank_pct, pred_ret=b.pred_ret_21d,
                      target_price=b.target_price, tech=tech, timing="enter_ok", exec_result=ex)
                if ex.ok:
                    rep.buys_executed += 1
            except Exception as e:
                rep.errors.append(f"buy {b.ticker}: {e}")
    return rep


def _risk_state(session: Session, broker: BrokerAdapter):
    from decision.runner import _default_risk_state
    return _default_risk_state(session, broker)
