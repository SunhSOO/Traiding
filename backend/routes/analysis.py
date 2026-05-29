"""Per-symbol analysis endpoint.

Single bundle endpoint so the UI doesn't waterfall N requests for one
symbol page. Returns:

- Security metadata
- Latest F / T / I scores
- ~30-day score history per module (for sparklines)
- Recent decisions for this ticker (~ last 20)
- Recent news mentions joined with their LLM classifications
  (when classified)
- Current index membership

All filtered to the (market, ticker) pair — never mixed across
markets, per the project's hard rule.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.models.audit import DecisionAudit
from core.models.classifications import ArticleClassification
from core.models.news import NewsArticle, NewsTickerMention
from core.models.prices import UniverseMembership
from core.models.scores import ModuleScore
from core.models.universe import Security
from core.security import CurrentUser
from core.types import Market

router = APIRouter()


# ── Response shapes ──
class SecurityOut(BaseModel):
    market: str
    ticker: str
    name: str
    exchange: Optional[str]
    sector: Optional[str]
    industry: Optional[str]
    currency: str
    is_active: bool


class ScoreSnapshotOut(BaseModel):
    score: Optional[float] = None
    confidence: Optional[float] = None
    computed_ts: Optional[str] = None
    model_version: Optional[str] = None


class ScoreHistoryPoint(BaseModel):
    ts: str
    score: float
    confidence: float


class DecisionOut(BaseModel):
    id: str
    decision_ts: str
    action: str
    composite_score: Optional[float]
    composite_confidence: Optional[float]


class NewsClassificationOut(BaseModel):
    event_type: str
    sentiment: str
    impact: str
    horizon: str
    confidence: float


class NewsItemOut(BaseModel):
    id: str
    title: str
    publisher: Optional[str]
    url: Optional[str]
    language: str
    published_ts: str
    source: str
    relevance: float
    classification: Optional[NewsClassificationOut] = None


class MembershipOut(BaseModel):
    index_code: str
    valid_from: str


class AnalysisResponse(BaseModel):
    security: SecurityOut
    current_scores: dict[str, ScoreSnapshotOut]
    score_history: dict[str, list[ScoreHistoryPoint]]
    recent_decisions: list[DecisionOut]
    recent_news: list[NewsItemOut]
    memberships: list[MembershipOut]


class DecisionMarkerOut(BaseModel):
    ts: str
    action: str
    composite_score: Optional[float] = None


class ScoreSeriesResponse(BaseModel):
    market: str
    ticker: str
    days: int
    series: dict[str, list[ScoreHistoryPoint]]  # F / T / I
    decision_markers: list[DecisionMarkerOut]    # for overlay annotations


# ── Route ──
@router.get("/{market}/{ticker}", response_model=AnalysisResponse)
async def analysis(
    market: str,
    ticker: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    history_days: int = 30,
    news_limit: int = 20,
    decisions_limit: int = 20,
) -> AnalysisResponse:
    try:
        m = Market(market.upper())
    except ValueError as e:
        raise HTTPException(400, f"unknown market: {market}") from e

    sec = await db.get(Security, (m.value, ticker))
    if sec is None:
        raise HTTPException(404, f"security not found: {m.value}:{ticker}")

    now = datetime.now(UTC)
    since = now - timedelta(days=history_days)

    # ── Current scores ──
    current_scores: dict[str, ScoreSnapshotOut] = {}
    history: dict[str, list[ScoreHistoryPoint]] = {"F": [], "T": [], "I": []}
    for module_code in ("F", "T", "I"):
        latest = await _latest_score(db, m, ticker, module_code)
        current_scores[module_code] = _snapshot(latest)
        rows = await _score_history(db, m, ticker, module_code, since)
        history[module_code] = [
            ScoreHistoryPoint(
                ts=r.computed_ts.isoformat(),
                score=float(r.score),
                confidence=float(r.confidence),
            )
            for r in rows
        ]

    # ── Recent decisions ──
    decisions_stmt = (
        select(DecisionAudit)
        .where(and_(DecisionAudit.market == m.value, DecisionAudit.ticker == ticker))
        .order_by(desc(DecisionAudit.decision_ts))
        .limit(decisions_limit)
    )
    decisions_result = await db.execute(decisions_stmt)
    recent_decisions = [
        DecisionOut(
            id=str(r.id), decision_ts=r.decision_ts.isoformat(), action=r.action,
            composite_score=_f(r.composite_score),
            composite_confidence=_f(r.composite_confidence),
        )
        for r in decisions_result.scalars()
    ]

    # ── Recent news mentions × classifications ──
    news_stmt = (
        select(
            NewsArticle.id, NewsArticle.title, NewsArticle.publisher,
            NewsArticle.url, NewsArticle.language, NewsArticle.published_ts,
            NewsArticle.source, NewsTickerMention.relevance,
        )
        .join(NewsTickerMention, and_(
            NewsTickerMention.article_id == NewsArticle.id,
            NewsTickerMention.article_published_ts == NewsArticle.published_ts,
        ))
        .where(and_(
            NewsTickerMention.market == m.value,
            NewsTickerMention.ticker == ticker,
            NewsArticle.published_ts >= since,
        ))
        .order_by(desc(NewsArticle.published_ts))
        .limit(news_limit)
    )
    news_result = await db.execute(news_stmt)
    news_rows = list(news_result.all())
    article_ids = [r[0] for r in news_rows]

    # Bulk-load latest classification per article (best-effort)
    classifications_by_id: dict = {}
    if article_ids:
        cls_stmt = (
            select(ArticleClassification)
            .where(and_(
                ArticleClassification.article_kind == "news",
                ArticleClassification.article_id.in_(article_ids),
            ))
        )
        cls_result = await db.execute(cls_stmt)
        for c in cls_result.scalars():
            # Keep the newest model_version when there's more than one
            existing = classifications_by_id.get(c.article_id)
            if existing is None or c.classified_ts > existing.classified_ts:
                classifications_by_id[c.article_id] = c

    recent_news = []
    for (aid, title, publisher, url, language, published_ts, source, relevance) in news_rows:
        cls_row = classifications_by_id.get(aid)
        cls_out = None
        if cls_row is not None and cls_row.error is None:
            cls_out = NewsClassificationOut(
                event_type=cls_row.event_type,
                sentiment=cls_row.sentiment,
                impact=cls_row.impact,
                horizon=cls_row.horizon,
                confidence=float(cls_row.confidence),
            )
        recent_news.append(NewsItemOut(
            id=str(aid), title=title, publisher=publisher, url=url,
            language=language, published_ts=published_ts.isoformat(),
            source=source, relevance=float(relevance),
            classification=cls_out,
        ))

    # ── Current memberships ──
    memb_stmt = (
        select(UniverseMembership)
        .where(and_(
            UniverseMembership.market == m.value,
            UniverseMembership.ticker == ticker,
            UniverseMembership.valid_to.is_(None),
        ))
    )
    memb_result = await db.execute(memb_stmt)
    memberships = [
        MembershipOut(index_code=r.index_code, valid_from=r.valid_from.isoformat())
        for r in memb_result.scalars()
    ]

    return AnalysisResponse(
        security=SecurityOut(
            market=sec.market, ticker=sec.ticker, name=sec.name,
            exchange=sec.exchange, sector=sec.sector, industry=sec.industry,
            currency=sec.currency, is_active=sec.is_active,
        ),
        current_scores=current_scores,
        score_history=history,
        recent_decisions=recent_decisions,
        recent_news=recent_news,
        memberships=memberships,
    )


@router.get("/{market}/{ticker}/score-history", response_model=ScoreSeriesResponse)
async def score_series(
    market: str,
    ticker: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    days: int = 365,
    max_points: int = 400,
) -> ScoreSeriesResponse:
    """Longer-history score series — used by the per-ticker overlay
    chart on the analysis page. Returned series are evenly downsampled
    to ``max_points`` per module so the SVG stays performant even at
    365-day windows."""
    try:
        m = Market(market.upper())
    except ValueError as e:
        raise HTTPException(400, f"unknown market: {market}") from e

    if days < 7 or days > 1825:  # 7d .. 5y
        raise HTTPException(400, "days must be between 7 and 1825")
    if max_points < 50 or max_points > 2000:
        raise HTTPException(400, "max_points must be between 50 and 2000")

    since = datetime.now(UTC) - timedelta(days=days)

    # Sanity check ticker exists (avoid leaking 'days' work on garbage input)
    sec = await db.get(Security, (m.value, ticker))
    if sec is None:
        raise HTTPException(404, f"security not found: {m.value}:{ticker}")

    series: dict[str, list[ScoreHistoryPoint]] = {}
    for module_code in ("F", "T", "I"):
        rows = await _score_history(db, m, ticker, module_code, since)
        points = [
            ScoreHistoryPoint(
                ts=r.computed_ts.isoformat(),
                score=float(r.score),
                confidence=float(r.confidence),
            )
            for r in rows
        ]
        series[module_code] = _downsample(points, max_points)

    # Decision markers — drop on the overlay so the operator can see
    # which signals corresponded to which actions.
    dec_stmt = (
        select(DecisionAudit.decision_ts, DecisionAudit.action,
               DecisionAudit.composite_score)
        .where(and_(
            DecisionAudit.market == m.value,
            DecisionAudit.ticker == ticker,
            DecisionAudit.decision_ts >= since,
        ))
        .order_by(DecisionAudit.decision_ts)
    )
    markers = [
        DecisionMarkerOut(
            ts=ts.isoformat(), action=action,
            composite_score=float(cs) if cs is not None else None,
        )
        for ts, action, cs in (await db.execute(dec_stmt)).all()
    ]

    return ScoreSeriesResponse(
        market=m.value, ticker=ticker, days=days,
        series=series, decision_markers=markers,
    )


def _downsample(points: list, target: int) -> list:
    """Evenly stride-sample a list, always keeping the first and last
    points so the chart endpoints don't lie."""
    n = len(points)
    if n <= target:
        return points
    # Stride pick + force endpoints
    step = n / target
    idxs = sorted({int(i * step) for i in range(target)} | {0, n - 1})
    return [points[i] for i in idxs if 0 <= i < n]


# ──────────────────────────────────────────────────────────────────────


async def _latest_score(
    db: AsyncSession, market: Market, ticker: str, module: str,
) -> Optional[ModuleScore]:
    stmt = (
        select(ModuleScore)
        .where(and_(
            ModuleScore.market == market.value,
            ModuleScore.ticker == ticker,
            ModuleScore.module == module,
        ))
        .order_by(desc(ModuleScore.computed_ts))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def _score_history(
    db: AsyncSession, market: Market, ticker: str, module: str, since: datetime,
) -> list[ModuleScore]:
    stmt = (
        select(ModuleScore)
        .where(and_(
            ModuleScore.market == market.value,
            ModuleScore.ticker == ticker,
            ModuleScore.module == module,
            ModuleScore.computed_ts >= since,
        ))
        .order_by(ModuleScore.computed_ts)
    )
    result = await db.execute(stmt)
    return list(result.scalars())


def _snapshot(row: Optional[ModuleScore]) -> ScoreSnapshotOut:
    if row is None:
        return ScoreSnapshotOut()
    return ScoreSnapshotOut(
        score=float(row.score),
        confidence=float(row.confidence),
        computed_ts=row.computed_ts.isoformat(),
        model_version=row.model_version,
    )


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None
