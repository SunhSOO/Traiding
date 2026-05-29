"""Decision endpoints — recent decisions + single-decision detail.

Phase 5 UI consumes these for the "결정 감사" (decision audit) view
where the operator can click any decision and see exactly why the
system chose to BUY / SELL / HOLD / REJECT — module scores, weights,
gates, risk snapshot, and execution result.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.models.audit import DecisionAudit, RiskSnapshot
from core.security import CurrentUser
from core.types import Market

router = APIRouter()


class DecisionListItem(BaseModel):
    id: str
    market: str
    ticker: str
    decision_ts: str
    action: str
    composite_score: Optional[float]
    composite_confidence: Optional[float]
    size_value: Optional[float]


class DecisionDetail(BaseModel):
    id: str
    market: str
    ticker: str
    decision_ts: str
    action: str
    composite_score: Optional[float]
    composite_confidence: Optional[float]
    fundamental_score: Optional[float]
    fundamental_confidence: Optional[float] = None
    technical_score: Optional[float]
    technical_confidence: Optional[float] = None
    information_score: Optional[float]
    information_confidence: Optional[float] = None
    weights: Optional[dict]
    gate_results: Optional[dict]
    inputs_snapshot: Optional[dict] = None
    size_value: Optional[float]
    size_currency: Optional[str]
    model_version: Optional[str] = None
    risk_snapshot: Optional[dict]
    execution_result: Optional[dict]
    error: Optional[str]
    reason: Optional[str] = None       # derived: "why this action?"
    contributions: Optional[list[dict]] = None  # [{module, score, weight, contribution}]


@router.get("/recent", response_model=list[DecisionListItem])
async def recent_decisions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None, description="KR | US"),
    ticker: Optional[str] = Query(None),
    action: Optional[str] = Query(None, description="BUY | SELL | HOLD | REJECTED"),
    limit: int = Query(100, ge=1, le=500),
) -> list[DecisionListItem]:
    from sqlalchemy import desc, select

    stmt = select(DecisionAudit).order_by(desc(DecisionAudit.decision_ts)).limit(limit)
    if market:
        try:
            m = Market(market.upper())
        except ValueError as e:
            raise HTTPException(400, f"unknown market: {market}") from e
        stmt = stmt.where(DecisionAudit.market == m.value)
    if ticker:
        stmt = stmt.where(DecisionAudit.ticker == ticker)
    if action:
        stmt = stmt.where(DecisionAudit.action == action.upper())
    result = await db.execute(stmt)
    rows = list(result.scalars())
    return [
        DecisionListItem(
            id=str(r.id), market=r.market, ticker=r.ticker,
            decision_ts=r.decision_ts.isoformat(),
            action=r.action,
            composite_score=float(r.composite_score) if r.composite_score is not None else None,
            composite_confidence=float(r.composite_confidence) if r.composite_confidence is not None else None,
            size_value=float(r.size_value) if r.size_value is not None else None,
        )
        for r in rows
    ]


@router.get("/{decision_id}", response_model=DecisionDetail)
async def decision_detail(
    decision_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> DecisionDetail:
    try:
        did = uuid.UUID(decision_id)
    except ValueError as e:
        raise HTTPException(400, "invalid decision id") from e

    row = await db.get(DecisionAudit, did)
    if row is None:
        raise HTTPException(404, "not found")

    risk_snapshot = None
    if row.risk_snapshot_id is not None:
        rs = await db.get(RiskSnapshot, row.risk_snapshot_id)
        if rs is not None:
            risk_snapshot = {
                "all_passed": rs.all_passed,
                "max_lot_pass": rs.max_lot_pass,
                "daily_loss_pass": rs.daily_loss_pass,
                "consecutive_loss_pass": rs.consecutive_loss_pass,
                "max_positions_pass": rs.max_positions_pass,
                "spread_pass": rs.spread_pass,
                "symbol_allowed_pass": rs.symbol_allowed_pass,
                "failures": rs.failures,
            }

    def _f(v):
        return float(v) if v is not None else None

    contributions = _contributions(row)
    reason = _derive_reason(row, risk_snapshot)

    return DecisionDetail(
        id=str(row.id), market=row.market, ticker=row.ticker,
        decision_ts=row.decision_ts.isoformat(),
        action=row.action,
        composite_score=_f(row.composite_score),
        composite_confidence=_f(row.composite_confidence),
        fundamental_score=_f(row.fundamental_score),
        fundamental_confidence=_f(row.fundamental_confidence),
        technical_score=_f(row.technical_score),
        technical_confidence=_f(row.technical_confidence),
        information_score=_f(row.information_score),
        information_confidence=_f(row.information_confidence),
        weights=row.weights, gate_results=row.gate_results,
        inputs_snapshot=row.inputs_snapshot,
        size_value=_f(row.size_value), size_currency=row.size_currency,
        model_version=row.model_version,
        risk_snapshot=risk_snapshot,
        execution_result=row.execution_result,
        error=row.error,
        reason=reason,
        contributions=contributions,
    )


def _contributions(row: DecisionAudit) -> list[dict]:
    """Break composite into per-module score × weight contributions.

    Returns empty list when weights are absent (e.g. very old audit
    rows before Phase 3 weight persistence)."""
    weights = row.weights or {}
    out: list[dict] = []
    for code, score in (
        ("F", row.fundamental_score),
        ("T", row.technical_score),
        ("I", row.information_score),
    ):
        if score is None:
            continue
        w = weights.get(code)
        if w is None:
            continue
        s = float(score)
        out.append({
            "module": code,
            "score": s,
            "weight": float(w),
            "contribution": s * float(w),
        })
    return out


def _derive_reason(row: DecisionAudit, risk_snapshot: Optional[dict]) -> str:
    """One-sentence explanation operator-friendly.

    Priorities: (1) risk-snapshot failure for REJECTED, (2) gate
    failure for HOLD, (3) composite vs thresholds for BUY/SELL, (4)
    score-too-weak for HOLD without gate failures."""
    action = row.action
    if action == "REJECTED" and risk_snapshot is not None:
        failed = [
            name for name, key in (
                ("최대 lot", "max_lot_pass"),
                ("일 손실", "daily_loss_pass"),
                ("연속 손실", "consecutive_loss_pass"),
                ("최대 포지션", "max_positions_pass"),
                ("스프레드", "spread_pass"),
                ("심볼 허용", "symbol_allowed_pass"),
            ) if risk_snapshot.get(key) is False
        ]
        if failed:
            return f"리스크 게이트 실패: {', '.join(failed)}"
        return "리스크 검사 실패"

    gate_results = row.gate_results or {}
    failed_gates = [
        k for k, v in gate_results.items()
        if isinstance(v, dict) and v.get("passed") is False
    ] if isinstance(gate_results, dict) else []

    if action == "HOLD":
        if failed_gates:
            return f"게이트 차단: {', '.join(failed_gates)}"
        if row.composite_score is None:
            return "composite 점수 계산 실패 (입력 부족)"
        cs = float(row.composite_score)
        return f"composite {cs:+.1f}이(가) 임계값 안 — 행동 조건 미충족"

    if action == "BUY" and row.composite_score is not None:
        return f"composite {float(row.composite_score):+.1f} ≥ BUY 임계값"
    if action == "SELL" and row.composite_score is not None:
        return f"composite {float(row.composite_score):+.1f} ≤ SELL 임계값"
    if action == "EXIT":
        return "포지션 청산 시그널"
    if action == "REDUCE":
        return "포지션 축소 시그널"
    return ""
