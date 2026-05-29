"""News explorer endpoints — articles + classifications + sentiment timeline.

Three reads:

- ``GET /articles`` — paged list with filters (market, ticker, source,
  date range, sentiment, event_type, impact). Joins classifications
  best-effort.
- ``GET /sentiment-timeline`` — per-day rollup of positive/neutral/
  negative counts so the UI can draw a sentiment ribbon over time.
- ``GET /event-mix`` — event_type distribution in a window (which kinds
  of news are dominating right now?).

Read-only. Classification triggering is the info-module's job."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_async_db
from core.models.classifications import ArticleClassification
from core.models.news import NewsArticle, NewsTickerMention
from core.security import CurrentUser
from core.types import Market

router = APIRouter()


class ArticleOut(BaseModel):
    id: str
    title: str
    publisher: Optional[str]
    url: Optional[str]
    language: str
    published_ts: str
    source: str
    summary: Optional[str] = None
    # Mention join (best-effort — None when no specific ticker filtered)
    market: Optional[str] = None
    ticker: Optional[str] = None
    relevance: Optional[float] = None
    # Classification join (best-effort)
    event_type: Optional[str] = None
    sentiment: Optional[str] = None
    impact: Optional[str] = None
    horizon: Optional[str] = None
    confidence: Optional[float] = None


class ArticleListOut(BaseModel):
    total: int       # filtered total, not capped by limit
    rows: list[ArticleOut]


class SentimentDayPoint(BaseModel):
    date: str        # YYYY-MM-DD
    positive: int = 0
    neutral: int = 0
    negative: int = 0


class SentimentTimelineOut(BaseModel):
    market: Optional[str]
    ticker: Optional[str]
    days: list[SentimentDayPoint]


class EventMixBucket(BaseModel):
    event_type: str
    count: int
    share: float


class EventMixOut(BaseModel):
    market: Optional[str]
    window_days: int
    total_classifications: int
    buckets: list[EventMixBucket]


# ──────────────────────────────────────────────────────────────────────


@router.get("/articles", response_model=ArticleListOut)
async def list_articles(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None, description="KR | US"),
    ticker: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    sentiment: Optional[str] = Query(None, description="POSITIVE | NEUTRAL | NEGATIVE"),
    event_type: Optional[str] = Query(None),
    impact: Optional[str] = Query(None, description="HIGH | MEDIUM | LOW"),
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ArticleListOut:
    market_value = _resolve_market(market)
    since = datetime.now(UTC) - timedelta(days=days)

    if ticker and market_value is None:
        raise HTTPException(400, "ticker filter requires market")

    base_filters = [NewsArticle.published_ts >= since]
    if source:
        base_filters.append(NewsArticle.source == source)

    # If a ticker is requested, restrict via NewsTickerMention join.
    if ticker:
        mention_subq = (
            select(NewsTickerMention.article_id, NewsTickerMention.relevance)
            .where(and_(
                NewsTickerMention.market == market_value,
                NewsTickerMention.ticker == ticker,
            ))
            .subquery()
        )
    else:
        mention_subq = None

    # Classifications join needs a deterministic latest pick per article;
    # we accept a best-effort latest by selecting the max(classified_ts).
    cls_subq = (
        select(
            ArticleClassification.article_id.label("article_id"),
            func.max(ArticleClassification.classified_ts).label("ts"),
        )
        .where(ArticleClassification.article_kind == "news")
        .group_by(ArticleClassification.article_id)
        .subquery()
    )

    stmt = (
        select(
            NewsArticle.id, NewsArticle.title, NewsArticle.publisher,
            NewsArticle.url, NewsArticle.language, NewsArticle.published_ts,
            NewsArticle.source, NewsArticle.summary,
            ArticleClassification.event_type,
            ArticleClassification.sentiment,
            ArticleClassification.impact,
            ArticleClassification.horizon,
            ArticleClassification.confidence,
            (mention_subq.c.relevance if mention_subq is not None else func.cast(None, NewsArticle.id.type)).label("relevance"),
        )
        .outerjoin(cls_subq, cls_subq.c.article_id == NewsArticle.id)
        .outerjoin(
            ArticleClassification,
            and_(
                ArticleClassification.article_id == NewsArticle.id,
                ArticleClassification.article_kind == "news",
                ArticleClassification.classified_ts == cls_subq.c.ts,
            ),
        )
        .where(and_(*base_filters))
        .order_by(desc(NewsArticle.published_ts))
    )

    if mention_subq is not None:
        stmt = stmt.join(mention_subq, mention_subq.c.article_id == NewsArticle.id)
    if sentiment:
        stmt = stmt.where(ArticleClassification.sentiment == sentiment.upper())
    if event_type:
        stmt = stmt.where(ArticleClassification.event_type == event_type.upper())
    if impact:
        stmt = stmt.where(ArticleClassification.impact == impact.upper())

    # Cheap "filtered total" — a separate count(*) over the same WHERE
    # (without classification field filters this becomes a hot path; if
    # it ever bites we'll cache the count in a freshness row).
    count_stmt = select(func.count()).select_from(NewsArticle).where(and_(*base_filters))
    if mention_subq is not None:
        count_stmt = count_stmt.join(mention_subq, mention_subq.c.article_id == NewsArticle.id)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.limit(limit).offset(offset)
    raw_rows = list((await db.execute(stmt)).all())

    out: list[ArticleOut] = []
    for row in raw_rows:
        (aid, title, publisher, url, language, published_ts, source_val,
         summary, ev_type, sent, imp, hor, conf, relevance) = row
        out.append(ArticleOut(
            id=str(aid), title=title, publisher=publisher, url=url,
            language=language, published_ts=published_ts.isoformat(),
            source=source_val, summary=summary,
            market=market_value if ticker else None,
            ticker=ticker if ticker else None,
            relevance=float(relevance) if relevance is not None else None,
            event_type=ev_type, sentiment=sent, impact=imp, horizon=hor,
            confidence=float(conf) if conf is not None else None,
        ))
    return ArticleListOut(total=int(total), rows=out)


@router.get("/sentiment-timeline", response_model=SentimentTimelineOut)
async def sentiment_timeline(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None, description="KR | US"),
    ticker: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
) -> SentimentTimelineOut:
    market_value = _resolve_market(market)
    if ticker and market_value is None:
        raise HTTPException(400, "ticker filter requires market")

    since = datetime.now(UTC) - timedelta(days=days)
    base_filters = [
        ArticleClassification.article_kind == "news",
        NewsArticle.published_ts >= since,
    ]

    stmt = (
        select(
            func.date(NewsArticle.published_ts).label("d"),
            ArticleClassification.sentiment,
            func.count().label("n"),
        )
        .join(NewsArticle, NewsArticle.id == ArticleClassification.article_id)
        .where(and_(*base_filters))
        .group_by("d", ArticleClassification.sentiment)
    )
    if ticker:
        stmt = stmt.join(
            NewsTickerMention,
            and_(
                NewsTickerMention.article_id == NewsArticle.id,
                NewsTickerMention.market == market_value,
                NewsTickerMention.ticker == ticker,
            ),
        )

    raw_rows = list((await db.execute(stmt)).all())
    by_day: dict[str, SentimentDayPoint] = {}
    for d, sent, n in raw_rows:
        key = d.isoformat() if isinstance(d, date) else str(d)
        pt = by_day.setdefault(key, SentimentDayPoint(date=key))
        if sent == "POSITIVE": pt.positive = int(n)
        elif sent == "NEGATIVE": pt.negative = int(n)
        else: pt.neutral = int(n)
    days_sorted = sorted(by_day.values(), key=lambda p: p.date)
    return SentimentTimelineOut(
        market=market_value, ticker=ticker, days=days_sorted,
    )


@router.get("/event-mix", response_model=EventMixOut)
async def event_mix(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    market: Optional[str] = Query(None, description="KR | US"),
    days: int = Query(7, ge=1, le=180),
) -> EventMixOut:
    market_value = _resolve_market(market)
    since = datetime.now(UTC) - timedelta(days=days)

    stmt = (
        select(ArticleClassification.event_type, func.count())
        .join(NewsArticle, NewsArticle.id == ArticleClassification.article_id)
        .where(and_(
            ArticleClassification.article_kind == "news",
            NewsArticle.published_ts >= since,
        ))
        .group_by(ArticleClassification.event_type)
    )
    if market_value:
        stmt = stmt.join(
            NewsTickerMention,
            and_(
                NewsTickerMention.article_id == NewsArticle.id,
                NewsTickerMention.market == market_value,
            ),
        )
    rows = list((await db.execute(stmt)).all())
    total = sum(int(n) for _, n in rows)
    buckets = [
        EventMixBucket(
            event_type=ev or "OTHER",
            count=int(n),
            share=(int(n) / total) if total else 0.0,
        )
        for ev, n in rows
    ]
    buckets.sort(key=lambda b: -b.count)
    return EventMixOut(
        market=market_value, window_days=days,
        total_classifications=total, buckets=buckets,
    )


def _resolve_market(market: Optional[str]) -> Optional[str]:
    if market is None:
        return None
    try:
        return Market(market.upper()).value
    except ValueError as e:
        raise HTTPException(400, f"unknown market: {market}") from e
