"""Pure signal-scan engine.

Reuses ``decision/composite.score_composite`` so the dry-run produces
exactly the same composite the production runner would. Threshold +
confidence + staleness gates apply in the same order. No risk
checks, no sizing — the goal is a *signal preview*, not a full
verdict."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

from decision.composite import ModuleVerdict, score_composite
from decision.types import Action, DecisionConfig


@dataclass(frozen=True)
class TickerScoreSnapshot:
    """Latest F/T/I points + name for one ticker, plus optional
    cluster mapping."""
    market: str
    ticker: str
    name: Optional[str] = None
    fundamental_ts: Optional[datetime] = None
    fundamental_score: Optional[float] = None
    fundamental_confidence: Optional[float] = None
    technical_ts: Optional[datetime] = None
    technical_score: Optional[float] = None
    technical_confidence: Optional[float] = None
    information_ts: Optional[datetime] = None
    information_score: Optional[float] = None
    information_confidence: Optional[float] = None
    cluster_id: Optional[str] = None


@dataclass(frozen=True)
class ScanRequest:
    """All knobs the scan can vary — defaults mirror DecisionConfig."""
    decision_config: DecisionConfig
    as_of: datetime
    buy_threshold: float = 25.0
    sell_threshold: float = -25.0
    min_overall_confidence: float = 0.40


@dataclass(frozen=True)
class ScanResult:
    market: str
    ticker: str
    name: Optional[str]
    cluster_id: Optional[str]
    composite_score: float
    composite_confidence: float
    action: Action
    reason: str
    fundamental_score: Optional[float] = None
    technical_score: Optional[float] = None
    information_score: Optional[float] = None
    contributing_modules: tuple[str, ...] = ()
    weights_used: dict[str, float] = field(default_factory=dict)
    stalest_module: Optional[str] = None
    stalest_age_hours: Optional[float] = None


# ──────────────────────────────────────────────────────────────────────


def scan_signals(
    snapshots: Iterable[TickerScoreSnapshot],
    *,
    request: ScanRequest,
) -> list[ScanResult]:
    """Walk snapshots → composite → action, returning one result per
    ticker. HOLD / REJECTED outcomes still appear in the result list
    so the operator can see *everything* the scan considered."""
    cfg = request.decision_config
    out: list[ScanResult] = []

    for snap in snapshots:
        result = _scan_one(snap, request)
        out.append(result)
    return out


def _scan_one(snap: TickerScoreSnapshot, request: ScanRequest) -> ScanResult:
    cfg = request.decision_config

    # Staleness gate per-module: if score is older than staleness window,
    # drop it (set score to None) so composite degrades gracefully.
    staleness = timedelta(hours=cfg.score_staleness_hours)

    f_score, f_conf = _within_window(
        snap.fundamental_ts, snap.fundamental_score, snap.fundamental_confidence,
        request.as_of, staleness,
    )
    t_score, t_conf = _within_window(
        snap.technical_ts, snap.technical_score, snap.technical_confidence,
        request.as_of, staleness,
    )
    i_score, i_conf = _within_window(
        snap.information_ts, snap.information_score, snap.information_confidence,
        request.as_of, staleness,
    )

    # Stalest module — useful operator signal even when action = HOLD
    ages = [
        ("F", snap.fundamental_ts), ("T", snap.technical_ts), ("I", snap.information_ts),
    ]
    stalest = max(
        ((m, _age_hours(ts, request.as_of)) for m, ts in ages if ts is not None),
        key=lambda p: p[1] if p[1] is not None else -1, default=(None, None),
    )

    composite = score_composite(
        fundamental=ModuleVerdict(score=f_score, confidence=f_conf),
        technical=ModuleVerdict(score=t_score, confidence=t_conf),
        information=ModuleVerdict(score=i_score, confidence=i_conf),
        config=cfg,
        cluster_id=snap.cluster_id,
    )

    # Reason resolution mirrors decision/gates.py priorities.
    if not composite.contributing_modules:
        action, reason = Action.HOLD, "기여 모듈 없음 (모든 점수가 stale 또는 결손)"
    elif composite.confidence < request.min_overall_confidence:
        action, reason = Action.HOLD, f"composite confidence {composite.confidence:.2f} < {request.min_overall_confidence}"
    elif composite.score >= request.buy_threshold:
        action, reason = Action.BUY, f"composite {composite.score:+.1f} ≥ BUY 임계값"
    elif composite.score <= request.sell_threshold:
        action, reason = Action.SELL, f"composite {composite.score:+.1f} ≤ SELL 임계값"
    else:
        action, reason = Action.HOLD, f"composite {composite.score:+.1f} 임계값 안"

    return ScanResult(
        market=snap.market, ticker=snap.ticker, name=snap.name,
        cluster_id=snap.cluster_id,
        composite_score=composite.score,
        composite_confidence=composite.confidence,
        action=action, reason=reason,
        fundamental_score=f_score, technical_score=t_score, information_score=i_score,
        contributing_modules=composite.contributing_modules,
        weights_used=composite.weights_used,
        stalest_module=stalest[0],
        stalest_age_hours=stalest[1],
    )


def _within_window(
    ts: Optional[datetime], score: Optional[float], conf: Optional[float],
    as_of: datetime, staleness: timedelta,
) -> tuple[Optional[float], Optional[float]]:
    if ts is None or score is None or conf is None:
        return None, None
    if (as_of - ts) > staleness:
        return None, None
    return score, conf


def _age_hours(ts: Optional[datetime], as_of: datetime) -> Optional[float]:
    if ts is None:
        return None
    return (as_of - ts).total_seconds() / 3600.0
