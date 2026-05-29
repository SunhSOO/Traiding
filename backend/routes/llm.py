"""LLM provider status + classification queue endpoints.

Operator visibility into:

- Configured providers (Ollama / Groq / Gemini) and connectivity
- Ollama-specific: ping latency, model availability, GPU info if exposed
- Classification queue: how many news articles haven't been classified
  yet, recent throughput, error rate, model_version mix

The classifier worker writes to ``article_classifications`` whether
or not it succeeded (failures land with ``error`` set), so we can
compute error rate without a separate log table."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.db import get_async_db
from core.models.classifications import ArticleClassification
from core.models.news import NewsArticle
from core.security import CurrentUser

router = APIRouter()


class ProviderStatus(BaseModel):
    name: str                       # ollama | groq | gemini
    configured: bool
    reachable: Optional[bool] = None
    latency_ms: Optional[float] = None
    detail: Optional[str] = None
    models: Optional[list[str]] = None
    error: Optional[str] = None


class ClassificationQueue(BaseModel):
    articles_total: int
    articles_classified: int
    articles_pending: int           # no classification row yet
    classified_24h: int
    classified_7d: int
    errors_24h: int
    error_rate_24h: float           # 0..1
    median_latency_seconds: Optional[float] = None   # classify_ts − published_ts


class ModelVersionBucket(BaseModel):
    model_version: str
    count: int
    last_used_ts: Optional[str] = None


class LLMHealthOut(BaseModel):
    checked_at: str
    providers: list[ProviderStatus]
    default_model: str
    queue: ClassificationQueue
    model_versions: list[ModelVersionBucket]


# ──────────────────────────────────────────────────────────────────────


@router.get("/health", response_model=LLMHealthOut)
async def llm_health(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    window_days: int = Query(7, ge=1, le=90),
) -> LLMHealthOut:
    settings = get_settings()
    providers: list[ProviderStatus] = []
    providers.append(await _check_ollama(settings.ollama_host))
    providers.append(ProviderStatus(
        name="groq",
        configured=settings.has_groq,
        reachable=None if not settings.has_groq else True,
        detail="키 설정됨" if settings.has_groq else "키 미설정",
    ))
    providers.append(ProviderStatus(
        name="gemini",
        configured=settings.has_gemini,
        reachable=None if not settings.has_gemini else True,
        detail="키 설정됨" if settings.has_gemini else "키 미설정",
    ))

    queue = await _build_queue_stats(db, window_days=window_days)
    versions = await _build_version_mix(db, window_days=window_days)

    return LLMHealthOut(
        checked_at=datetime.now(UTC).isoformat(),
        providers=providers,
        default_model=settings.ollama_default_model,
        queue=queue,
        model_versions=versions,
    )


# ──────────────────────────────────────────────────────────────────────


async def _check_ollama(host: str) -> ProviderStatus:
    if not host:
        return ProviderStatus(name="ollama", configured=False, detail="host 미설정")
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{host.rstrip('/')}/api/tags")
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if resp.status_code != 200:
            return ProviderStatus(
                name="ollama", configured=True, reachable=False,
                latency_ms=elapsed_ms,
                error=f"HTTP {resp.status_code}",
            )
        data = resp.json()
        models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        return ProviderStatus(
            name="ollama", configured=True, reachable=True,
            latency_ms=elapsed_ms, models=models,
            detail=f"{len(models)} 모델 로드됨",
        )
    except Exception as e:
        elapsed_ms = (time.monotonic() - started) * 1000.0
        return ProviderStatus(
            name="ollama", configured=True, reachable=False,
            latency_ms=elapsed_ms, error=str(e)[:200],
        )


async def _build_queue_stats(
    db: AsyncSession, *, window_days: int,
) -> ClassificationQueue:
    now = datetime.now(UTC)
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)
    since_window = now - timedelta(days=window_days)

    # Total articles in window
    articles_total = (await db.execute(
        select(func.count()).select_from(NewsArticle)
        .where(NewsArticle.published_ts >= since_window)
    )).scalar() or 0

    # Distinct article_ids that have at least one classification row
    # (regardless of model_version or error state)
    classified = (await db.execute(
        select(func.count(func.distinct(ArticleClassification.article_id)))
        .select_from(ArticleClassification)
        .join(NewsArticle, NewsArticle.id == ArticleClassification.article_id)
        .where(and_(
            ArticleClassification.article_kind == "news",
            NewsArticle.published_ts >= since_window,
        ))
    )).scalar() or 0

    classified_24h = (await db.execute(
        select(func.count()).select_from(ArticleClassification)
        .where(and_(
            ArticleClassification.article_kind == "news",
            ArticleClassification.classified_ts >= since_24h,
        ))
    )).scalar() or 0

    classified_7d = (await db.execute(
        select(func.count()).select_from(ArticleClassification)
        .where(and_(
            ArticleClassification.article_kind == "news",
            ArticleClassification.classified_ts >= since_7d,
        ))
    )).scalar() or 0

    errors_24h = (await db.execute(
        select(func.count()).select_from(ArticleClassification)
        .where(and_(
            ArticleClassification.article_kind == "news",
            ArticleClassification.classified_ts >= since_24h,
            ArticleClassification.error.is_not(None),
        ))
    )).scalar() or 0

    error_rate = (errors_24h / classified_24h) if classified_24h else 0.0

    # Median latency: classify_ts − published_ts
    # We approximate with a small sample to avoid a full table scan.
    latency_rows = list((await db.execute(
        select(
            ArticleClassification.classified_ts,
            NewsArticle.published_ts,
        )
        .join(NewsArticle, NewsArticle.id == ArticleClassification.article_id)
        .where(and_(
            ArticleClassification.article_kind == "news",
            ArticleClassification.classified_ts >= since_24h,
            ArticleClassification.error.is_(None),
        ))
        .order_by(desc(ArticleClassification.classified_ts))
        .limit(200)
    )).all())
    latencies = sorted(
        (cls_ts - pub_ts).total_seconds()
        for cls_ts, pub_ts in latency_rows
        if cls_ts and pub_ts
    )
    median = latencies[len(latencies) // 2] if latencies else None

    return ClassificationQueue(
        articles_total=int(articles_total),
        articles_classified=int(classified),
        articles_pending=max(int(articles_total) - int(classified), 0),
        classified_24h=int(classified_24h),
        classified_7d=int(classified_7d),
        errors_24h=int(errors_24h),
        error_rate_24h=float(error_rate),
        median_latency_seconds=median,
    )


async def _build_version_mix(
    db: AsyncSession, *, window_days: int,
) -> list[ModelVersionBucket]:
    since = datetime.now(UTC) - timedelta(days=window_days)
    rows = list((await db.execute(
        select(
            ArticleClassification.model_version,
            func.count(),
            func.max(ArticleClassification.classified_ts),
        )
        .where(and_(
            ArticleClassification.article_kind == "news",
            ArticleClassification.classified_ts >= since,
        ))
        .group_by(ArticleClassification.model_version)
    )).all())
    rows.sort(key=lambda r: -int(r[1]))
    return [
        ModelVersionBucket(
            model_version=mv,
            count=int(n),
            last_used_ts=last.isoformat() if last else None,
        )
        for mv, n, last in rows
    ]
