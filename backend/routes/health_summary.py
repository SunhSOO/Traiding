"""Aggregated system-health summary endpoint.

One round-trip for the dashboard widget — collapses freshness +
LLM + drift + risk + scheduler + price-gap into a single tier
classification per area:

- ``OK``     — green; everything's fine
- ``WARN``   — yellow; degraded but not breaking
- ``ERROR``  — red; needs operator attention

Tier rules are conservative and explicit so the operator can
reverse-engineer "why is data WARN today?" without grepping logs."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.data_quality import (
    FRESHNESS_THRESHOLDS, find_gaps, is_weekday, score_freshness,
)
from core.config import get_settings
from core.db import get_async_db
from core.models.audit import DecisionAudit, DataFreshness, RiskSnapshot
from core.models.classifications import ArticleClassification
from core.models.prices import DailyPrice
from core.models.universe import Security
from core.security import CurrentUser
from decision.drift import check_action_mix_drift

router = APIRouter()

Tier = Literal["OK", "WARN", "ERROR"]


class HealthArea(BaseModel):
    name: str
    tier: Tier
    headline: str
    detail: Optional[str] = None


class HealthSummary(BaseModel):
    checked_at: str
    overall_tier: Tier
    areas: list[HealthArea]


# ──────────────────────────────────────────────────────────────────────


@router.get("", response_model=HealthSummary)
async def health_summary(
    user: CurrentUser,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> HealthSummary:
    areas: list[HealthArea] = []
    areas.append(await _check_freshness(db))
    areas.append(await _check_prices(db))
    areas.append(await _check_llm())
    areas.append(await _check_classifications(db))
    areas.append(await _check_risk(db))
    areas.append(await _check_drift(db))
    areas.append(_check_scheduler(request))

    overall = _aggregate_tier([a.tier for a in areas])
    return HealthSummary(
        checked_at=datetime.now(UTC).isoformat(),
        overall_tier=overall,
        areas=areas,
    )


# ──────────────────────────────────────────────────────────────────────


async def _check_freshness(db: AsyncSession) -> HealthArea:
    """Data sources via ``data_freshness`` table."""
    now = datetime.now(UTC)
    rows = list((await db.execute(select(DataFreshness))).scalars())
    total = len(rows)
    if total == 0:
        return HealthArea(
            name="데이터 소스", tier="WARN",
            headline="아직 잡 실행 기록 없음",
            detail="universe / prices / macro 잡이 한 번도 돌지 않았습니다.",
        )
    errored = sum(1 for r in rows if r.last_error)
    stale = sum(
        1 for r in rows
        if not r.last_error and (
            r.last_success_ts is None
            or (now - r.last_success_ts) > timedelta(hours=24)
        )
    )
    ok = total - errored - stale
    if errored > 0:
        return HealthArea(
            name="데이터 소스", tier="ERROR",
            headline=f"{errored}개 소스 에러",
            detail=f"정상 {ok} · 지연 {stale} · 에러 {errored} (총 {total})",
        )
    if stale > 0:
        return HealthArea(
            name="데이터 소스", tier="WARN",
            headline=f"{stale}개 소스 지연 (>24h)",
            detail=f"정상 {ok} · 지연 {stale} (총 {total})",
        )
    return HealthArea(
        name="데이터 소스", tier="OK",
        headline=f"{ok}개 소스 정상",
    )


async def _check_prices(db: AsyncSession) -> HealthArea:
    """Per-ticker price freshness — counts DEAD/STALE."""
    today = datetime.now(UTC).date()
    sec_rows = list((await db.execute(
        select(Security.market, Security.ticker).where(Security.is_active.is_(True))
    )).all())
    if not sec_rows:
        return HealthArea(
            name="가격 데이터", tier="WARN",
            headline="활성 종목 없음",
            detail="universe 잡이 돌아야 합니다.",
        )
    last_dates = dict((
        ((m, t), d) for m, t, d in (await db.execute(
            select(
                DailyPrice.market, DailyPrice.ticker,
                func.max(DailyPrice.trade_date),
            ).group_by(DailyPrice.market, DailyPrice.ticker)
        )).all()
    ))
    dead = stale = fresh = 0
    for m, t in sec_rows:
        fs = score_freshness(
            market=m, ticker=t,
            last_price_date=last_dates.get((m, t)),
            today=today,
        )
        if fs.tier == "DEAD": dead += 1
        elif fs.tier == "STALE": stale += 1
        else: fresh += 1
    total = len(sec_rows)
    if dead / total > 0.05:
        return HealthArea(
            name="가격 데이터", tier="ERROR",
            headline=f"DEAD {dead}개 종목 ({dead/total*100:.0f}%)",
            detail=f"신선 {fresh} · 지연 {stale} · DEAD {dead}",
        )
    if (dead + stale) / total > 0.1:
        return HealthArea(
            name="가격 데이터", tier="WARN",
            headline=f"문제 {stale + dead}개 종목",
            detail=f"신선 {fresh} · 지연 {stale} · DEAD {dead}",
        )
    return HealthArea(
        name="가격 데이터", tier="OK",
        headline=f"{fresh}/{total} 종목 신선",
    )


async def _check_llm() -> HealthArea:
    """Ollama ping. Other providers skipped (paid APIs)."""
    settings = get_settings()
    if not settings.has_ollama:
        return HealthArea(
            name="LLM (Ollama)", tier="WARN",
            headline="Ollama host 미설정",
            detail="OLLAMA_HOST 환경변수 또는 설정 파일 필요",
        )
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.ollama_host.rstrip('/')}/api/tags")
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if resp.status_code != 200:
            return HealthArea(
                name="LLM (Ollama)", tier="ERROR",
                headline=f"HTTP {resp.status_code}",
                detail=f"latency {elapsed_ms:.0f}ms",
            )
        models = (resp.json().get("models") or [])
        has_default = any(
            m.get("name", "").startswith(settings.ollama_default_model.split(":")[0])
            for m in models
        )
        if not has_default:
            return HealthArea(
                name="LLM (Ollama)", tier="WARN",
                headline=f"기본 모델 ({settings.ollama_default_model}) 미로드",
                detail=f"{len(models)} 모델 로드됨 · {elapsed_ms:.0f}ms",
            )
        if elapsed_ms > 1500:
            return HealthArea(
                name="LLM (Ollama)", tier="WARN",
                headline=f"응답 느림 {elapsed_ms:.0f}ms",
                detail=f"{len(models)} 모델 로드됨",
            )
        return HealthArea(
            name="LLM (Ollama)", tier="OK",
            headline=f"{len(models)}개 모델 · {elapsed_ms:.0f}ms",
        )
    except Exception as e:
        return HealthArea(
            name="LLM (Ollama)", tier="ERROR",
            headline="연결 실패",
            detail=str(e)[:200],
        )


async def _check_classifications(db: AsyncSession) -> HealthArea:
    """24h classification error rate."""
    since = datetime.now(UTC) - timedelta(hours=24)
    total = (await db.execute(
        select(func.count()).select_from(ArticleClassification)
        .where(and_(
            ArticleClassification.article_kind == "news",
            ArticleClassification.classified_ts >= since,
        ))
    )).scalar() or 0
    if total == 0:
        return HealthArea(
            name="분류 처리", tier="WARN",
            headline="24h 분류 기록 없음",
            detail="LLM classifier가 작동하지 않습니다.",
        )
    errors = (await db.execute(
        select(func.count()).select_from(ArticleClassification)
        .where(and_(
            ArticleClassification.article_kind == "news",
            ArticleClassification.classified_ts >= since,
            ArticleClassification.error.is_not(None),
        ))
    )).scalar() or 0
    err_rate = errors / total
    if err_rate > 0.2:
        return HealthArea(
            name="분류 처리", tier="ERROR",
            headline=f"에러율 {err_rate*100:.1f}% ({errors}/{total})",
        )
    if err_rate > 0.05:
        return HealthArea(
            name="분류 처리", tier="WARN",
            headline=f"에러율 {err_rate*100:.1f}% ({errors}/{total})",
        )
    return HealthArea(
        name="분류 처리", tier="OK",
        headline=f"24h {total}건 분류",
        detail=f"에러율 {err_rate*100:.2f}%",
    )


async def _check_risk(db: AsyncSession) -> HealthArea:
    """7d risk gate fail rate."""
    since = datetime.now(UTC) - timedelta(days=7)
    total = (await db.execute(
        select(func.count()).select_from(RiskSnapshot)
        .where(RiskSnapshot.snapshot_ts >= since)
    )).scalar() or 0
    if total == 0:
        return HealthArea(
            name="리스크 게이트", tier="OK",
            headline="최근 7일 검사 없음",
            detail="결정 사이클이 실행되지 않았거나 아직 거래 시그널 없음",
        )
    passed = (await db.execute(
        select(func.count()).select_from(RiskSnapshot)
        .where(and_(
            RiskSnapshot.snapshot_ts >= since,
            RiskSnapshot.all_passed.is_(True),
        ))
    )).scalar() or 0
    pass_rate = passed / total
    if pass_rate < 0.5:
        return HealthArea(
            name="리스크 게이트", tier="ERROR",
            headline=f"통과율 {pass_rate*100:.1f}% ({passed}/{total})",
            detail="리스크 한도가 너무 빡빡하거나 비정상 시그널 발생",
        )
    if pass_rate < 0.8:
        return HealthArea(
            name="리스크 게이트", tier="WARN",
            headline=f"통과율 {pass_rate*100:.1f}% ({passed}/{total})",
        )
    return HealthArea(
        name="리스크 게이트", tier="OK",
        headline=f"{total}건 중 {passed}건 통과",
    )


async def _check_drift(db: AsyncSession) -> HealthArea:
    """Action-mix drift across 24h vs 30d."""
    now = datetime.now(UTC)
    today_actions = [
        a for a, in (await db.execute(
            select(DecisionAudit.action)
            .where(DecisionAudit.decision_ts >= now - timedelta(hours=24))
        )).all()
    ]
    hist_actions = [
        a for a, in (await db.execute(
            select(DecisionAudit.action)
            .where(and_(
                DecisionAudit.decision_ts >= now - timedelta(days=30),
                DecisionAudit.decision_ts < now - timedelta(hours=24),
            ))
        )).all()
    ]
    check = check_action_mix_drift(
        today_actions=today_actions,
        history_actions=hist_actions,
        tv_threshold=0.25,
        min_today=5,
    )
    if not today_actions or not hist_actions:
        return HealthArea(
            name="결정 drift", tier="OK",
            headline="기준 데이터 부족",
            detail=f"오늘 {len(today_actions)}건 · 기준 {len(hist_actions)}건",
        )
    if check.is_anomaly:
        return HealthArea(
            name="결정 drift", tier="WARN",
            headline=f"TV distance {check.total_variation_distance:.2f}",
            detail="오늘 액션 분포가 30일 기준에서 크게 벗어남",
        )
    return HealthArea(
        name="결정 drift", tier="OK",
        headline=f"TV distance {check.total_variation_distance:.2f}",
    )


def _check_scheduler(request: Request) -> HealthArea:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return HealthArea(
            name="스케줄러", tier="ERROR",
            headline="미실행",
            detail="lifespan에서 시작에 실패했습니다.",
        )
    if scheduler.is_paused():
        return HealthArea(
            name="스케줄러", tier="WARN",
            headline="일시정지됨",
            detail="kill switch 또는 운영자가 멈춤",
        )
    jobs = scheduler.status()
    return HealthArea(
        name="스케줄러", tier="OK",
        headline=f"{len(jobs)}개 잡 구동 중",
    )


def _aggregate_tier(tiers: list[Tier]) -> Tier:
    if any(t == "ERROR" for t in tiers):
        return "ERROR"
    if any(t == "WARN" for t in tiers):
        return "WARN"
    return "OK"
