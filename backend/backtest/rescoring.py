"""Re-scoring backtest — apply *current* (or operator-supplied) weights
to *historical* module scores and replay the resulting decisions.

This is the answer to "would the system we have today have done
better last year than the system we had last year?":

1. Walk historical module_scores in chronological windows.
2. For each (market, ticker, point-in-time), find the F / T / I
   scores **as-of that timestamp** (no look-ahead).
3. Run them through ``score_composite`` using whichever weights the
   caller specifies — *current* learned weights, default weights, or
   an experimental override.
4. Translate composite score + confidence + thresholds into a
   synthetic action (BUY / SELL / HOLD) using the same gate logic
   the production runner uses.
5. Hand the resulting BUY/SELL stream to the existing replay
   simulator → equity curve.

Pure functions live here; the DB-bound wrapper lives in
``rescoring_runner``. Tests cover the action mapping logic without
touching SQLAlchemy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Literal, Optional

from backtest.replay import DecisionSignal
from decision.composite import ModuleVerdict, score_composite
from decision.types import Action, DecisionConfig


@dataclass(frozen=True)
class ModuleScorePoint:
    """One historical module-score reading."""
    ts: datetime
    market: str
    ticker: str
    module: str           # F / T / I
    score: float          # -100..+100
    confidence: float     # 0..1
    model_version: Optional[str] = None


@dataclass(frozen=True)
class RescoringConfig:
    """Knobs that shape the synthetic decision stream."""
    buy_threshold: float = 25.0
    sell_threshold: float = -25.0
    min_overall_confidence: float = 0.40
    decision_cooldown_days: int = 1
    # Optional ticker → cluster_id map. When set, the matching cluster's
    # learned weights are used; tickers with no cluster fall back to the
    # global weights in ``decision_config``.
    ticker_cluster_map: dict[tuple[str, str], str] = field(default_factory=dict)


@dataclass(frozen=True)
class RescoredDecision:
    """A synthetic decision the re-scoring engine produced."""
    ts: datetime
    market: str
    ticker: str
    action: Action
    composite_score: float
    composite_confidence: float
    cluster_id: Optional[str] = None
    weights_used: dict[str, float] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────


def rescore_to_decisions(
    score_points: Iterable[ModuleScorePoint],
    *,
    decision_config: DecisionConfig,
    rescoring_config: Optional[RescoringConfig] = None,
) -> list[RescoredDecision]:
    """Walk all score points chronologically, group by (ticker, day),
    and emit a synthetic decision whenever:

    - All three modules have a score within the freshness window
      (we use the latest within ``decision_config.score_staleness_hours``
      before the current point), AND
    - The composite > buy_threshold or < sell_threshold, AND
    - Overall confidence ≥ min_overall_confidence, AND
    - We haven't already produced a decision for this ticker within
      the cooldown window.

    Output is sorted chronologically — feed directly to
    ``backtest.replay.replay``."""
    cfg = rescoring_config or RescoringConfig()
    points = sorted(score_points, key=lambda p: p.ts)
    if not points:
        return []

    # latest[(market, ticker, module)] → most recent ModuleScorePoint so far
    latest: dict[tuple[str, str, str], ModuleScorePoint] = {}
    # last_decision_ts[(market, ticker)] → ts of last emitted decision
    last_decision_ts: dict[tuple[str, str], datetime] = {}

    out: list[RescoredDecision] = []
    staleness = timedelta(hours=decision_config.score_staleness_hours)

    for pt in points:
        # Update the latest-per-module state
        key = (pt.market, pt.ticker, pt.module)
        prev = latest.get(key)
        if prev is None or pt.ts > prev.ts:
            latest[key] = pt

        # Try to emit a decision for this (market, ticker) using the
        # state we know up to now.
        cluster_id = cfg.ticker_cluster_map.get((pt.market, pt.ticker))
        f_pt = latest.get((pt.market, pt.ticker, "F"))
        t_pt = latest.get((pt.market, pt.ticker, "T"))
        i_pt = latest.get((pt.market, pt.ticker, "I"))

        # Freshness gate — required only for modules with non-zero weight.
        # When the learned/overridden weight for a module is 0 the engine
        # ignores its score anyway, so demanding its freshness would
        # over-constrain (e.g. T-only learned clusters need not wait for
        # F/I news to land).
        active_weights = decision_config.weights_for(cluster_id)
        if active_weights.get("F", 0.0) > 0 and not _fresh_enough(f_pt, pt.ts, staleness): continue
        if active_weights.get("T", 0.0) > 0 and not _fresh_enough(t_pt, pt.ts, staleness): continue
        if active_weights.get("I", 0.0) > 0 and not _fresh_enough(i_pt, pt.ts, staleness): continue

        composite = score_composite(
            fundamental=_to_verdict(f_pt),
            technical=_to_verdict(t_pt),
            information=_to_verdict(i_pt),
            config=decision_config,
            cluster_id=cluster_id,
        )

        # Confidence gate
        if composite.confidence < cfg.min_overall_confidence:
            continue

        # Action threshold
        action = _action_from_composite(composite.score, cfg)
        if action == Action.HOLD:
            continue

        # Cooldown
        prev_ts = last_decision_ts.get((pt.market, pt.ticker))
        if prev_ts is not None:
            if (pt.ts - prev_ts) < timedelta(days=cfg.decision_cooldown_days):
                continue

        out.append(RescoredDecision(
            ts=pt.ts, market=pt.market, ticker=pt.ticker,
            action=action,
            composite_score=composite.score,
            composite_confidence=composite.confidence,
            cluster_id=cluster_id,
            weights_used=composite.weights_used,
        ))
        last_decision_ts[(pt.market, pt.ticker)] = pt.ts

    return out


def to_replay_signals(decisions: Iterable[RescoredDecision]) -> list[DecisionSignal]:
    """Adapter — RescoredDecision → DecisionSignal so the existing
    replay engine consumes them unchanged."""
    return [
        DecisionSignal(
            ts=d.ts, market=d.market, ticker=d.ticker, action=d.action.value,
        )
        for d in decisions
    ]


# ──────────────────────────────────────────────────────────────────────


def _to_verdict(pt: Optional[ModuleScorePoint]) -> ModuleVerdict:
    if pt is None:
        return ModuleVerdict()
    return ModuleVerdict(
        score=pt.score, confidence=pt.confidence,
        model_version=pt.model_version,
    )


def _fresh_enough(
    pt: Optional[ModuleScorePoint], at: datetime, staleness: timedelta,
) -> bool:
    if pt is None:
        return False
    return (at - pt.ts) <= staleness


def _action_from_composite(score: float, cfg: RescoringConfig) -> Action:
    if score >= cfg.buy_threshold:
        return Action.BUY
    if score <= cfg.sell_threshold:
        return Action.SELL
    return Action.HOLD
