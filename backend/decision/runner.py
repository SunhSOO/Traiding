"""Decision runner — orchestrate everything for one batch.

For each ticker in a market:
1. Load latest F/T/I module scores (look-ahead-safe).
2. Compose → composite score + confidence.
3. Run gates → if any fail, action = HOLD, persist audit, move on.
4. If composite passes BUY/SELL thresholds → sizer → OrderIntent.
5. RiskEngine.check (6 hard limits) → if fail, action = REJECTED.
6. If risk passes → PaperBroker.execute → action = BUY/SELL.
7. Always persist DecisionAudit + RiskSnapshot.

Every step is injectable so tests can swap fakes. The default
production wiring is in :func:`build_default_runner`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from brokers.base import BrokerAdapter, OrderIntent, OrderSide, OrderType
from core.as_of import require_as_of
from core.audit import (
    DecisionRecord, ModuleScore as AuditModuleScore,
    record_decision_with_risk,
)
from core.logging import get_logger
from core.models.audit import DecisionAudit
from core.models.prices import DailyPrice
from core.models.scores import ModuleScore
from core.models.universe import Security
from core.risk import RiskEngine, RiskLimits, RiskState
from core.types import Market
from decision.composite import ModuleVerdict, score_composite
from decision.drift import check_score_drift
from decision.gates import run_gates
from decision.sizer import SizerInputs, size_position
from decision.types import Action, DecisionConfig, DecisionVerdict

log = get_logger(__name__)


@dataclass
class DecisionRunReport:
    market: Market
    tickers_considered: int = 0
    tickers_traded: int = 0
    tickers_held: int = 0
    tickers_rejected: int = 0
    tickers_errored: int = 0
    errors: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────


@require_as_of
def run_decisions(
    session: Session,
    *,
    market: Market,
    as_of: datetime,
    broker: BrokerAdapter,
    risk_engine: RiskEngine,
    risk_limits: RiskLimits,
    config: Optional[DecisionConfig] = None,
    tickers: Optional[list[str]] = None,
    risk_state_factory=None,
    use_learned_weights: bool = True,
) -> DecisionRunReport:
    """Decision loop for one market.

    Parameters
    ----------
    broker : BrokerAdapter
        Where executions go — paper in dev, live (deferred) in prod.
    risk_engine, risk_limits : RiskEngine, RiskLimits
        Phase 0.5 hard-limit checker.
    risk_state_factory : callable, optional
        () → RiskState. Defaults to a function that reads broker
        positions + recent paper trades. Tests pass a fake.
    use_learned_weights : bool
        If True (default), load Phase 3 cluster_weights + ticker_clusters
        from the DB and apply per-ticker. If False, every ticker uses
        the global default weights in ``config``.
    """
    config = config or DecisionConfig()
    report = DecisionRunReport(market=market)

    # ── Phase 3 integration: load latest learned weights + ticker→cluster map ──
    # Operator overrides must apply even when Phase 3 hasn't produced learned
    # weights yet — they are the emergency knob, not an addition to learning.
    cluster_lookup: dict[tuple[str, str], str] = {}
    if use_learned_weights:
        try:
            from training.registry import load_current_assignments, load_latest_weights

            latest_weights = load_latest_weights(session) or {}
            assignments = load_current_assignments(session, market=market.value)
            operator_overrides = _load_operator_overrides(session)

            # Merge: learned first, operator overrides take precedence.
            merged_weights: dict[str, dict[str, float]] = dict(latest_weights)
            for cid, w in operator_overrides.items():
                merged_weights[cid] = w

            if merged_weights:
                from dataclasses import replace
                config = replace(config, cluster_weight_overrides=merged_weights)
                cluster_lookup = assignments
                if operator_overrides:
                    log.warning(
                        "decision.operator_overrides_applied",
                        market=market.value,
                        overridden_clusters=list(operator_overrides),
                    )
                log.info(
                    "decision.using_learned_weights",
                    market=market.value,
                    learned_clusters=len(latest_weights),
                    operator_overrides=len(operator_overrides),
                    total_clusters=len(merged_weights),
                    assigned_tickers=len(assignments),
                )
        except Exception as e:
            log.warning("decision.learned_weights_load_failed",
                        error=str(e), note="falling back to global defaults")
    if tickers is None:
        tickers = _active_tickers(session, market)

    risk_state_factory = risk_state_factory or (lambda: _default_risk_state(session, broker))
    account_snapshot = broker.get_account()

    # Look up the latest regime label for this market once (cheap; one
    # row lookup). All ticker decisions in this loop share the same
    # regime — that's the right behaviour because regime is a market-
    # level state, not a per-ticker one.
    current_regime = _latest_regime_label(session, market)
    if current_regime:
        log.info(
            "decision.regime_applied",
            market=market.value, regime=current_regime,
            buy_thr=config.buy_threshold_for_regime(current_regime),
            sell_thr=config.sell_threshold_for_regime(current_regime),
            size_frac=config.size_fraction_for_regime(current_regime),
        )

    for ticker in tickers:
        try:
            cluster_id = cluster_lookup.get((market.value, ticker))
            verdict, intent = _decide_one(
                session, market=market, ticker=ticker, as_of=as_of,
                config=config, account=account_snapshot,
                cluster_id=cluster_id, regime=current_regime,
            )
        except Exception as e:
            log.exception("decision.compute_failed", market=market.value, ticker=ticker)
            report.tickers_errored += 1
            report.errors.append(f"{ticker}: {e}")
            continue

        report.tickers_considered += 1

        # If verdict says HOLD/abstain → no broker, no risk check; still audit.
        if intent is None:
            _persist_hold(session, market, ticker, as_of, verdict)
            report.tickers_held += 1
            continue

        # Risk check
        risk_state = risk_state_factory()
        risk_result = risk_engine.check(intent, risk_state, risk_limits)
        if not risk_result.all_passed:
            verdict_rejected = DecisionVerdict(
                action=Action.REJECTED,
                composite_score=verdict.composite_score,
                composite_confidence=verdict.composite_confidence,
                size_value=verdict.size_value, size_currency=verdict.size_currency,
                reason="risk_engine_rejected",
                gate_results=verdict.gate_results,
                sizer_breakdown=verdict.sizer_breakdown,
            )
            _persist_with_risk(
                session, market, ticker, as_of, verdict_rejected, risk_result,
            )
            report.tickers_rejected += 1
            continue

        # Execute on broker
        exec_result = broker.execute(intent)
        verdict_executed = DecisionVerdict(
            action=verdict.action,
            composite_score=verdict.composite_score,
            composite_confidence=verdict.composite_confidence,
            size_value=verdict.size_value, size_currency=verdict.size_currency,
            reason=verdict.reason if exec_result.ok else f"broker_failed: {exec_result.error}",
            gate_results=verdict.gate_results,
            sizer_breakdown=verdict.sizer_breakdown,
        )
        _persist_with_risk(
            session, market, ticker, as_of, verdict_executed, risk_result,
            execution_result={
                "ok": exec_result.ok,
                "fill_price": exec_result.fill_price,
                "fill_volume": exec_result.fill_volume,
                "error": exec_result.error,
            },
        )
        if exec_result.ok:
            report.tickers_traded += 1
        else:
            report.tickers_rejected += 1

    log.info(
        "decision.run_done", market=market.value,
        considered=report.tickers_considered, traded=report.tickers_traded,
        held=report.tickers_held, rejected=report.tickers_rejected,
        errored=report.tickers_errored,
    )
    return report


# ──────────────────────────────────────────────────────────────────────
# Per-ticker decision logic
# ──────────────────────────────────────────────────────────────────────


def _decide_one(
    session: Session, *,
    market: Market, ticker: str, as_of: datetime,
    config: DecisionConfig, account,
    cluster_id: Optional[str] = None,
    regime: Optional[str] = None,
) -> tuple[DecisionVerdict, Optional[OrderIntent]]:
    """Return (verdict, intent). intent is None for HOLD/REJECTED-here paths.

    Parameters
    ----------
    regime : str, optional
        Latest market regime label ("RISK_ON" | "NEUTRAL" | "RISK_OFF").
        When supplied, BUY/SELL thresholds and position sizing scale
        through ``DecisionConfig.{buy,sell}_threshold_for_regime`` /
        ``size_fraction_for_regime``. None → unscaled (no-op).
    """
    # 1. Load latest F/T/I scores
    f_mv, f_ts = _latest_module(session, market, ticker, "F", as_of)
    t_mv, t_ts = _latest_module(session, market, ticker, "T", as_of)
    i_mv, i_ts = _latest_module(session, market, ticker, "I", as_of)
    freshest_ts = max([t for t in (f_ts, t_ts, i_ts) if t is not None], default=None)
    staleness_hours = (
        (as_of - freshest_ts).total_seconds() / 3600.0 if freshest_ts else None
    )

    composite = score_composite(
        fundamental=f_mv, technical=t_mv, information=i_mv,
        config=config, cluster_id=cluster_id,
    )

    # 2. Gates
    last_decision_ts = _last_decision_ts(session, market, ticker)
    gates = run_gates(
        composite=composite, config=config,
        last_decision_ts=last_decision_ts, now=as_of,
        score_max_age_hours=staleness_hours,
    )

    if not gates.all_passed:
        return (
            DecisionVerdict(
                action=Action.HOLD,
                composite_score=composite.score,
                composite_confidence=composite.confidence,
                reason=f"gates_failed: {','.join(gates.failed_gates)}",
                gate_results={
                    "all_passed": False, "failed": list(gates.failed_gates),
                    "notes": gates.notes,
                    "per_module": composite.per_module,
                },
            ),
            None,
        )

    # 3. Threshold check (regime-scaled)
    effective_buy_thr = config.buy_threshold_for_regime(regime)
    effective_sell_thr = config.sell_threshold_for_regime(regime)

    if composite.score >= effective_buy_thr:
        side = OrderSide.BUY
        action = Action.BUY
    elif composite.score <= effective_sell_thr:
        side = OrderSide.SELL
        action = Action.SELL
    else:
        return (
            DecisionVerdict(
                action=Action.HOLD,
                composite_score=composite.score,
                composite_confidence=composite.confidence,
                reason=(
                    f"below_action_thresholds (regime={regime or 'NONE'}, "
                    f"buy_thr={effective_buy_thr:.1f}, sell_thr={effective_sell_thr:.1f})"
                ),
                gate_results={
                    "all_passed": True, "per_module": composite.per_module,
                    "regime": regime,
                    "effective_buy_threshold": effective_buy_thr,
                    "effective_sell_threshold": effective_sell_thr,
                },
            ),
            None,
        )

    # 4. Size
    price_row = _latest_price(session, market, ticker, as_of)
    atr = _recent_atr(session, market, ticker, as_of)
    if price_row is None:
        return (
            DecisionVerdict(
                action=Action.HOLD,
                composite_score=composite.score,
                composite_confidence=composite.confidence,
                reason="no_recent_price",
                gate_results={"per_module": composite.per_module},
            ),
            None,
        )

    ticker_ccy = "KRW" if market is Market.KR else "USD"

    # Currency-match guard. Paper trading keeps one account per market
    # (default-kr KRW / default-us USD) so the sizer never has to
    # convert. A mismatched call almost always means the wrong account
    # was passed; refuse rather than silently produce a notional in the
    # wrong currency. FX-aware sizing belongs to a future multi-market
    # account model, not the per-market paper path.
    if ticker_ccy != account.base_currency:
        return (
            DecisionVerdict(
                action=Action.HOLD,
                composite_score=composite.score,
                composite_confidence=composite.confidence,
                reason=(
                    f"currency_mismatch: account={account.base_currency} "
                    f"vs ticker={ticker_ccy} — use per-market account"
                ),
                gate_results={"per_module": composite.per_module},
            ),
            None,
        )

    sizer_in = SizerInputs(
        account_equity=account.equity,
        account_currency=account.base_currency,
        current_price=float(price_row.close),
        ticker_currency=ticker_ccy,
        composite_score=composite.score,
        composite_confidence=composite.confidence,
        recent_atr=atr,
        fx_to_account=1.0,    # always 1.0 by construction (currencies match)
    )
    # Apply regime size scaler by overriding base_position_fraction on
    # a shallow config clone. The sizer reads this knob directly; no
    # other config field needs to change for size attenuation.
    sizer_config = config
    if regime:
        from dataclasses import replace as dc_replace
        sizer_config = dc_replace(
            config,
            base_position_fraction=config.size_fraction_for_regime(regime),
        )
    sizer_out = size_position(sizer_in, config=sizer_config)
    if sizer_out.shares <= 0:
        return (
            DecisionVerdict(
                action=Action.HOLD,
                composite_score=composite.score,
                composite_confidence=composite.confidence,
                reason="sizer_returned_zero",
                gate_results={"per_module": composite.per_module},
                sizer_breakdown=sizer_out.breakdown,
            ),
            None,
        )

    # 5. Build intent
    intent = OrderIntent(
        market=market, ticker=ticker, side=side,
        order_type=OrderType.MARKET, volume=float(sizer_out.shares),
        comment=f"woonam {action.value} composite={composite.score:.1f}",
    )
    verdict = DecisionVerdict(
        action=action,
        composite_score=composite.score,
        composite_confidence=composite.confidence,
        size_value=float(sizer_out.shares),
        size_currency=ticker_ccy,
        reason="composite_passed_threshold",
        gate_results={
            "all_passed": True, "per_module": composite.per_module,
            "weights": composite.weights_used,
            "cluster_id": cluster_id,
            "regime": regime,
            "effective_buy_threshold": effective_buy_thr,
            "effective_sell_threshold": effective_sell_thr,
            "effective_size_fraction": sizer_config.base_position_fraction,
        },
        sizer_breakdown=sizer_out.breakdown,
    )
    return (verdict, intent)


# ──────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────


def _persist_hold(
    session: Session, market: Market, ticker: str,
    as_of: datetime, verdict: DecisionVerdict,
) -> None:
    """HOLD never triggers risk engine, so we record decision only."""
    from core.audit import record_decision

    record = _build_audit_record(market, ticker, as_of, verdict)
    record_decision(session, record)


def _persist_with_risk(
    session: Session, market: Market, ticker: str,
    as_of: datetime, verdict: DecisionVerdict, risk_result,
    execution_result: Optional[dict] = None,
) -> None:
    record = _build_audit_record(market, ticker, as_of, verdict, execution_result)
    record_decision_with_risk(session, record, risk_result)


def _build_audit_record(
    market: Market, ticker: str, as_of: datetime, verdict: DecisionVerdict,
    execution_result: Optional[dict] = None,
) -> DecisionRecord:
    per_module = verdict.gate_results.get("per_module", {})
    return DecisionRecord(
        market=market, ticker=ticker, action=verdict.action.value,
        decision_ts=as_of,
        fundamental=_audit_module(per_module.get("F")),
        technical=_audit_module(per_module.get("T")),
        information=_audit_module(per_module.get("I")),
        composite_score=verdict.composite_score,
        composite_confidence=verdict.composite_confidence,
        weights=verdict.gate_results.get("weights"),
        gate_results={
            "passed": verdict.gate_results.get("all_passed", False),
            "failed": verdict.gate_results.get("failed", []),
            "notes": verdict.gate_results.get("notes", {}),
        },
        size_value=verdict.size_value,
        size_currency=verdict.size_currency,
        inputs_snapshot={"sizer": verdict.sizer_breakdown, "reason": verdict.reason},
        execution_result=execution_result,
    )


def _audit_module(info: Optional[dict]) -> AuditModuleScore:
    if not info or not info.get("present"):
        return AuditModuleScore(score=None, confidence=None, inputs={})
    return AuditModuleScore(
        score=info.get("score"),
        confidence=info.get("confidence"),
        inputs={"weight": info.get("weight"), "model_version": info.get("model_version")},
    )


# ──────────────────────────────────────────────────────────────────────
# DB lookups
# ──────────────────────────────────────────────────────────────────────


def _active_tickers(session: Session, market: Market) -> list[str]:
    stmt = (
        select(Security.ticker)
        .where(and_(Security.market == market.value, Security.is_active.is_(True)))
        .order_by(Security.ticker)
    )
    return [r[0] for r in session.execute(stmt)]


def _latest_module(
    session: Session, market: Market, ticker: str, module: str, as_of: datetime,
) -> tuple[ModuleVerdict, Optional[datetime]]:
    stmt = (
        select(ModuleScore)
        .where(and_(
            ModuleScore.market == market.value,
            ModuleScore.ticker == ticker,
            ModuleScore.module == module,
            ModuleScore.computed_ts <= as_of,
        ))
        .order_by(desc(ModuleScore.computed_ts))
        .limit(1)
    )
    row = session.scalars(stmt).first()
    if row is None:
        return (ModuleVerdict(), None)
    return (
        ModuleVerdict(
            score=float(row.score),
            confidence=float(row.confidence),
            model_version=row.model_version,
        ),
        row.computed_ts,
    )


def _last_decision_ts(session: Session, market: Market, ticker: str) -> Optional[datetime]:
    stmt = (
        select(DecisionAudit.decision_ts)
        .where(and_(
            DecisionAudit.market == market.value,
            DecisionAudit.ticker == ticker,
            DecisionAudit.action.in_([Action.BUY.value, Action.SELL.value]),
        ))
        .order_by(desc(DecisionAudit.decision_ts))
        .limit(1)
    )
    return session.scalar(stmt)


def _latest_price(session: Session, market: Market, ticker: str, as_of: datetime):
    stmt = (
        select(DailyPrice)
        .where(and_(
            DailyPrice.market == market.value,
            DailyPrice.ticker == ticker,
            DailyPrice.as_of_ts <= as_of,
        ))
        .order_by(desc(DailyPrice.trade_date))
        .limit(1)
    )
    return session.scalars(stmt).first()


def _recent_atr(session: Session, market: Market, ticker: str, as_of: datetime) -> Optional[float]:
    """Compute ATR(14) over the last 30 daily bars (look-ahead-safe)."""
    stmt = (
        select(DailyPrice)
        .where(and_(
            DailyPrice.market == market.value,
            DailyPrice.ticker == ticker,
            DailyPrice.as_of_ts <= as_of,
        ))
        .order_by(desc(DailyPrice.trade_date))
        .limit(30)
    )
    rows = list(session.scalars(stmt))
    if len(rows) < 15:
        return None
    rows.reverse()
    from technical.indicators import _atr
    atr_series = _atr(
        [float(r.high) for r in rows],
        [float(r.low) for r in rows],
        [float(r.close) for r in rows],
        period=14,
    )
    return atr_series[-1]


def _latest_regime_label(session: Session, market: Market) -> Optional[str]:
    """Return the most recent ``market_regime.label`` for this market,
    or None if the regime classifier hasn't run yet (so the runner
    falls back to unscaled thresholds + sizes)."""
    try:
        from core.models.regime import MarketRegime
        row = session.execute(
            select(MarketRegime.label)
            .where(MarketRegime.market == market.value)
            .order_by(desc(MarketRegime.ts))
            .limit(1)
        ).first()
        return row[0] if row else None
    except Exception as e:
        log.warning("decision.regime_load_failed", error=str(e))
        return None


def _load_operator_overrides(session: Session) -> dict[str, dict[str, float]]:
    """Read every row from ``cluster_weight_overrides`` and return the
    same shape ``cluster_weight_overrides`` field expects in
    DecisionConfig: ``{cluster_id: {"F": ..., "T": ..., "I": ...}}``.

    Returns empty dict on any DB error so a missing/empty table
    doesn't break the runner."""
    try:
        from core.models.overrides import ClusterWeightOverride
        from sqlalchemy import select as _sel
        rows = list(session.execute(_sel(ClusterWeightOverride)).scalars())
        return {
            r.cluster_id: {
                "F": float(r.w_fundamental),
                "T": float(r.w_technical),
                "I": float(r.w_information),
            }
            for r in rows
        }
    except Exception as e:
        log.warning("decision.operator_overrides_load_failed", error=str(e))
        return {}


def _default_risk_state(session: Session, broker: BrokerAdapter) -> RiskState:
    """Read open positions from broker + recent P&L for daily/streak metrics."""
    positions = broker.get_positions()
    account = broker.get_account()
    # Daily P&L: equity - initial would be all-time. For "today's"
    # we'd need a session-start baseline; for now use 0 placeholder so
    # the daily-loss gate is permissive in paper mode. Phase 6 wires up.
    return RiskState(
        open_position_count=len(positions),
        daily_pnl=0.0,
        consecutive_losses=0,
        current_spread_bps=10.0,        # placeholder — real spread feed in Phase 5
    )
