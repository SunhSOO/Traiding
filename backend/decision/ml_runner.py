"""ML decision runner — route production-model picks through the real
gates/risk/paper-broker pipeline, with regime-based bear defense.

Mirrors `decision.runner.run_decisions` but the signal comes from the
trained `ProductionRecommender` (rank model + quantile target price) instead
of the composite F/T/I rule. Reuses the existing `RiskEngine` and
`BrokerAdapter` (PaperBroker in dev) and writes `decision_audit` rows, so ML
trades flow through the same risk limits, execution and audit trail.

Bear defense: when the latest market regime is risk_off/crisis the runner
goes DEFENSIVE — it opens no new longs and (optionally) closes existing ones,
i.e. moves to cash. This addresses the long-only drawdown weakness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from brokers.base import BrokerAdapter, OrderIntent, OrderSide, OrderType
from core.models.audit import DecisionAudit
from core.risk import RiskEngine, RiskLimits
from core.types import Market
from decision.production_inference import ProductionRecommender, SizingConfig

DEFENSIVE_REGIMES = ("risk_off", "crisis")


@dataclass
class MLRunReport:
    market: str
    regime: Optional[str] = None
    defensive: bool = False
    buys_executed: int = 0
    sells_executed: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)


def _latest_regime(session: Session, market: str) -> Optional[str]:
    row = session.execute(text(
        "SELECT label FROM market_regime WHERE market=:m ORDER BY ts DESC LIMIT 1"
    ), {"m": market}).first()
    return str(row[0]).lower() if row else None


def run_ml_decisions(
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
    n_long: int = 20,
    regime_gate: bool = True,
    close_on_defensive: bool = True,
    cfg: SizingConfig = SizingConfig(),
) -> MLRunReport:
    rep = MLRunReport(market=market.value)
    rep.regime = _latest_regime(session, market.value)
    rep.defensive = bool(regime_gate and rep.regime in DEFENSIVE_REGIMES)

    account = broker.get_account()
    ccy = account.base_currency
    positions = {p.ticker: p for p in broker.get_positions()}
    # True equity = cash + marked position value (get_account().equity returns
    # cash only). Size against total equity but spend only available cash.
    cash = float(account.balance)
    pos_value = sum((p.current_price or p.entry_price) * p.volume for p in positions.values())
    equity = cash + pos_value

    recs = recommender.recommend(feat_df, close_map, cfg=cfg)
    rmap = {r["ticker"]: r for _, r in recs.iterrows()}

    def audit(ticker, action, size_value, r, exec_result, rejected=False):
        snap = {}
        if r is not None:
            snap = {"rank_pct": float(r["rank_pct"]), "pred_ret_21d": float(r["pred_ret"]),
                    "last_close": float(r["last_close"]), "target_price": float(r["target_price"]),
                    "band_low": float(r["band_low"]), "band_high": float(r["band_high"])}
        snap["regime"] = rep.regime
        snap["defensive"] = rep.defensive
        session.add(DecisionAudit(
            market=market.value, ticker=str(ticker), decision_ts=as_of,
            composite_score=float(round((r["rank_pct"]-0.5)*200, 3)) if r is not None else None,
            action=action, size_value=float(round(size_value, 6)) if size_value else None,
            size_currency=ccy, model_version=f"production_{market.value}_v1",
            inputs_snapshot=snap,
            execution_result=(None if exec_result is None else {
                "ok": exec_result.ok, "fill_price": exec_result.fill_price,
                "fill_volume": exec_result.fill_volume, "error": exec_result.error}),
            error=("risk_rejected" if rejected else None),
        ))

    # 1) SELLs — close positions in the bottom decile, or ALL if defensive.
    to_close = set()
    if rep.defensive and close_on_defensive:
        to_close = set(positions)
    else:
        to_close = {t for t in positions if rmap.get(t) is not None and rmap[t]["action"] == "SELL"}
    for ticker in to_close:
        pos = positions[ticker]
        intent = OrderIntent(market=market, ticker=ticker, side=OrderSide.SELL,
                             order_type=OrderType.MARKET, volume=pos.volume,
                             comment="ml_exit" + ("_defensive" if rep.defensive else ""))
        try:
            ex = broker.execute(intent)
            audit(ticker, "SELL", None, rmap.get(ticker), ex)
            if ex.ok:
                rep.sells_executed += 1
        except Exception as e:
            rep.errors.append(f"sell {ticker}: {e}")

    # 2) BUYs — top picks, equal weight, unless defensive (stay in cash).
    if not rep.defensive:
        # refresh cash after any sells
        avail = float(broker.get_account().balance)
        buys = recs[recs["action"] == "BUY"].head(n_long)
        per_name = equity / max(n_long, 1)          # target weight per name
        for _, r in buys.iterrows():
            ticker = r["ticker"]
            if ticker in positions:
                continue  # already long
            price = float(r["last_close"])
            if not price or price <= 0:
                continue
            size_value = min(per_name, avail)        # spend only available cash
            if size_value < equity * 0.005:          # too small to bother
                continue
            volume = size_value / price
            avail -= size_value
            tp = float(r["target_price"])
            intent = OrderIntent(market=market, ticker=str(ticker), side=OrderSide.BUY,
                                 order_type=OrderType.MARKET, volume=volume, tp=tp,
                                 comment="ml_long")
            risk = risk_engine.check(intent, _risk_state(session, broker), risk_limits)
            if not risk.all_passed:
                audit(ticker, "REJECTED", size_value, r, None, rejected=True)
                rep.rejected += 1
                continue
            try:
                ex = broker.execute(intent)
                audit(ticker, "BUY", size_value, r, ex)
                if ex.ok:
                    rep.buys_executed += 1
            except Exception as e:
                rep.errors.append(f"buy {ticker}: {e}")
    return rep


def _risk_state(session: Session, broker: BrokerAdapter):
    from decision.runner import _default_risk_state
    return _default_risk_state(session, broker)
