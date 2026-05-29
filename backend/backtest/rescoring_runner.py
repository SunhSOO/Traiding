"""DB-bound wrapper for the re-scoring backtest.

Loads:
- All ``module_scores`` rows in the requested window (optionally
  market / ticker / model_version filtered).
- Latest learned cluster weights from ``cluster_weights``.
- Current ticker → cluster mapping from ``ticker_clusters``.

Then runs the pure ``rescore_to_decisions`` engine + ``replay``.
The output is a ``BacktestRun`` with the same shape as the
signal-replay path, so the existing route serialiser is reused."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backtest.replay import replay
from backtest.rescoring import (
    ModuleScorePoint, RescoringConfig, rescore_to_decisions, to_replay_signals,
)
from backtest.runner import BacktestRun
from brokers.paper_equity import (
    TradeRecord, build_equity_curve, summarize,
)
from core.models.prices import DailyPrice
from core.models.scores import ModuleScore
from core.models.training import ClusterWeights, TickerClusterAssignment
from decision.types import DecisionConfig


async def run_rescoring_backtest(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    market: Optional[str] = None,
    tickers: Optional[list[str]] = None,
    initial_balance: float = 100_000.0,
    position_fraction: float = 0.05,
    buy_threshold: float = 25.0,
    sell_threshold: float = -25.0,
    min_overall_confidence: float = 0.40,
    decision_cooldown_days: int = 1,
    score_staleness_hours: int = 48,
    use_learned_weights: bool = True,
    weight_override: Optional[dict[str, float]] = None,
    restrict_to_indices: Optional[list[str]] = None,
) -> BacktestRun:
    """Re-score historical module_scores + simulate the resulting trades.

    Parameters
    ----------
    use_learned_weights
        When True, populates ``cluster_weight_overrides`` from the
        latest row per cluster in ``cluster_weights``. When False, the
        engine uses the global default weights for every ticker.
    weight_override
        ``{"F": 0.4, "T": 0.4, "I": 0.2}``-shaped override applied
        regardless of cluster. Mutually exclusive with
        ``use_learned_weights`` (override wins if both supplied).
    """
    if end <= start:
        raise ValueError("end must be after start")

    # 1. Load DecisionConfig with appropriate weights
    decision_config = await _build_decision_config(
        db,
        score_staleness_hours=score_staleness_hours,
        use_learned_weights=use_learned_weights,
        weight_override=weight_override,
    )

    # 2. Load ticker → cluster map
    cluster_map = (
        await _load_cluster_map(db) if use_learned_weights else {}
    )

    # 3. Pull module_scores in window
    scores_stmt = (
        select(
            ModuleScore.market, ModuleScore.ticker, ModuleScore.module,
            ModuleScore.computed_ts, ModuleScore.score,
            ModuleScore.confidence, ModuleScore.model_version,
        )
        .where(and_(
            ModuleScore.computed_ts >= start,
            ModuleScore.computed_ts <= end,
        ))
        .order_by(ModuleScore.computed_ts)
    )
    if market:
        scores_stmt = scores_stmt.where(ModuleScore.market == market)
    if tickers:
        scores_stmt = scores_stmt.where(ModuleScore.ticker.in_(tickers))

    score_rows = list((await db.execute(scores_stmt)).all())
    score_points = [
        ModuleScorePoint(
            ts=ts, market=mkt, ticker=tkr, module=mod,
            score=float(s), confidence=float(c),
            model_version=mv,
        )
        for mkt, tkr, mod, ts, s, c, mv in score_rows
    ]

    # 4. Re-score
    rescoring_config = RescoringConfig(
        buy_threshold=buy_threshold,
        sell_threshold=sell_threshold,
        min_overall_confidence=min_overall_confidence,
        decision_cooldown_days=decision_cooldown_days,
        ticker_cluster_map=cluster_map,
    )
    decisions = rescore_to_decisions(
        score_points,
        decision_config=decision_config,
        rescoring_config=rescoring_config,
    )

    # 4.5 Survivorship-bias guard.
    # When ``restrict_to_indices`` is set, drop decisions for tickers
    # that were NOT members of the named index on the decision date.
    # The membership history table answers this in O(1) after a one-
    # time load over the entire window — even a 5-year backtest is a
    # couple thousand rows.
    if restrict_to_indices and decisions:
        decisions = await _filter_decisions_by_membership(
            db, decisions, market=market, index_codes=restrict_to_indices,
        )

    # 5. Replay via price oracle
    signals = to_replay_signals(decisions)
    price_lookup = await _load_prices(
        db,
        keys={(d.market, d.ticker) for d in decisions},
        start=start, end=end,
    )

    def price_at(m: str, t: str, d: date):
        # Walk back up to 7 calendar days to skip weekends/holidays.
        for offset in range(8):
            v = price_lookup.get((m, t, d - timedelta(days=offset)))
            if v is not None:
                return v
        return None

    result = replay(
        signals,
        price_at=price_at,
        initial_balance=initial_balance,
        position_fraction=position_fraction,
    )

    # 6. Curve + summary
    records = [
        TradeRecord(exit_ts=t.exit_ts, pnl=t.pnl) for t in result.closed_trades
    ]
    curve = build_equity_curve(records, initial_balance=initial_balance)
    summary = summarize(records, curve, initial_balance=initial_balance)

    return BacktestRun(result=result, curve=curve, summary=summary)


# ──────────────────────────────────────────────────────────────────────


async def _build_decision_config(
    db: AsyncSession,
    *,
    score_staleness_hours: int,
    use_learned_weights: bool,
    weight_override: Optional[dict[str, float]],
) -> DecisionConfig:
    if weight_override:
        # An override forces a single global triple. We bake it into the
        # default fields and clear cluster overrides so every ticker uses it.
        total = sum(weight_override.values()) or 1.0
        return DecisionConfig(
            weight_fundamental=weight_override.get("F", 0.0) / total,
            weight_technical=weight_override.get("T", 0.0) / total,
            weight_information=weight_override.get("I", 0.0) / total,
            score_staleness_hours=score_staleness_hours,
        )

    if not use_learned_weights:
        return DecisionConfig(score_staleness_hours=score_staleness_hours)

    cluster_overrides = await _load_latest_cluster_weights(db)
    return DecisionConfig(
        score_staleness_hours=score_staleness_hours,
        cluster_weight_overrides=cluster_overrides,
    )


async def _load_latest_cluster_weights(db: AsyncSession) -> dict[str, dict[str, float]]:
    """Latest row per cluster_id from ``cluster_weights``."""
    rows = list((await db.execute(
        select(
            ClusterWeights.cluster_id,
            ClusterWeights.w_fundamental,
            ClusterWeights.w_technical,
            ClusterWeights.w_information,
            ClusterWeights.learned_at,
        )
        .order_by(ClusterWeights.learned_at.desc())
    )).all())
    out: dict[str, dict[str, float]] = {}
    for cluster_id, wf, wt, wi, _ts in rows:
        if cluster_id not in out:
            out[cluster_id] = {
                "F": float(wf), "T": float(wt), "I": float(wi),
            }
    return out


async def _load_cluster_map(db: AsyncSession) -> dict[tuple[str, str], str]:
    """Current ticker → cluster_id from ``ticker_clusters`` (latest per ticker)."""
    rows = list((await db.execute(
        select(
            TickerClusterAssignment.market,
            TickerClusterAssignment.ticker,
            TickerClusterAssignment.cluster_id,
            TickerClusterAssignment.assigned_at,
        )
        .order_by(TickerClusterAssignment.assigned_at.desc())
    )).all())
    seen: set[tuple[str, str]] = set()
    out: dict[tuple[str, str], str] = {}
    for mkt, tkr, cluster_id, _ts in rows:
        key = (mkt, tkr)
        if key in seen:
            continue
        seen.add(key)
        out[key] = cluster_id
    return out


async def _filter_decisions_by_membership(
    db: AsyncSession, decisions, *,
    market: Optional[str], index_codes: list[str],
):
    """Drop synthetic decisions where the ticker wasn't in the named
    index on the decision date. Walks one async DB query against
    UniverseMembership rather than the sync ``MembershipFilter`` so
    we stay inside the async session."""
    if not decisions:
        return decisions
    from sqlalchemy import or_
    from core.models.prices import UniverseMembership

    markets = {market} if market else {d.market for d in decisions}
    rows = list((await db.execute(
        select(
            UniverseMembership.market,
            UniverseMembership.ticker,
            UniverseMembership.valid_from,
            UniverseMembership.valid_to,
        ).where(and_(
            UniverseMembership.market.in_(markets),
            UniverseMembership.index_code.in_(index_codes),
        ))
    )).all())
    by_key: dict[tuple[str, str], list[tuple]] = {}
    for mkt, tkr, vf, vt in rows:
        by_key.setdefault((mkt, tkr), []).append((vf, vt))

    def was_member(d) -> bool:
        ranges = by_key.get((d.market, d.ticker))
        if not ranges:
            return False
        on = d.ts.date() if hasattr(d.ts, "date") else d.ts
        for vf, vt in ranges:
            if vf <= on and (vt is None or vt > on):
                return True
        return False

    return [d for d in decisions if was_member(d)]


async def _load_prices(
    db: AsyncSession, *, keys: set[tuple[str, str]],
    start: datetime, end: datetime,
) -> dict[tuple[str, str, date], float]:
    if not keys:
        return {}
    from sqlalchemy import or_

    clauses = [
        and_(DailyPrice.market == m, DailyPrice.ticker == t) for m, t in keys
    ]
    rows = await db.execute(
        select(
            DailyPrice.market, DailyPrice.ticker,
            DailyPrice.trade_date, DailyPrice.close,
        )
        .where(and_(
            or_(*clauses),
            DailyPrice.trade_date >= (start - timedelta(days=5)).date(),
            DailyPrice.trade_date <= (end + timedelta(days=5)).date(),
        ))
    )
    return {(m, t, d): float(c) for m, t, d, c in rows}
