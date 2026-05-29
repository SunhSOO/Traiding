"""Information runner — classify articles + score tickers.

Two distinct loops:

1. **classify_pending(...)** — pull articles that don't yet have a
   classification at the current model_version, send each through the
   LLM classifier, persist into ``article_classifications``. Bounded
   per-call (``max_articles``) so the GPU isn't monopolised.

2. **score_market(...)** — for each active ticker, gather classified
   mentions in the lookback window, build :class:`WeightedMention`
   objects (applying source trust + time decay), feed to
   :func:`score_information`, persist into ``module_scores``
   (module='I').

These are independent jobs. The scheduler runs `classify_pending`
hourly and `score_market` daily.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import and_, desc, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.as_of import require_as_of
from core.config import get_settings
from core.llm import LLMProvider, OllamaProvider
from core.logging import get_logger
from core.models.classifications import ArticleClassification as ArticleClassificationORM
from core.models.disclosures import Disclosure
from core.models.news import NewsArticle, NewsTickerMention
from core.models.scores import ModuleScore
from core.models.universe import Security
from core.types import Market
from information.classifier import classify_article
from information.score import score_information
from information.trust import WeightedMention, source_trust, time_decay_weight
from information.types import (
    ArticleClassification as ClassValue,
    Horizon, Impact, Sentiment,
)

log = get_logger(__name__)


@dataclass
class ClassifyReport:
    articles_seen: int = 0
    articles_classified: int = 0
    articles_failed: int = 0
    model_version: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass
class InformationScoreReport:
    market: Market
    tickers_processed: int = 0
    tickers_skipped_no_mentions: int = 0
    rows_written: int = 0


# ──────────────────────────────────────────────────────────────────────
# Classification loop
# ──────────────────────────────────────────────────────────────────────


def classify_pending(
    session: Session,
    *,
    max_articles: int = 50,
    lookback_days: int = 7,
    provider: Optional[LLMProvider] = None,
    ticker_whitelist: Optional[Iterable[str]] = None,
) -> ClassifyReport:
    """Pull recent unclassified news articles and classify them.

    Priority order:
    1. Articles with rule-based ticker mentions (already known to be
       about our universe).
    2. Articles without mentions, time-ordered newest first.

    Stops after ``max_articles`` per call so GPU isn't monopolised.
    """
    provider = provider or _default_provider()
    model_version = _model_version_string(provider)
    report = ClassifyReport(model_version=model_version)
    now = datetime.now(UTC)
    since = now - timedelta(days=lookback_days)

    whitelist = list(ticker_whitelist) if ticker_whitelist is not None else _active_ticker_set(session)

    pending = _select_unclassified_news(session, since=since, model_version=model_version,
                                        limit=max_articles)
    report.articles_seen = len(pending)
    if not pending:
        return report

    for art in pending:
        try:
            result = classify_article(
                title=art.title, summary=art.summary or "",
                publisher=art.publisher, language=art.language,
                published_iso=art.published_ts.isoformat(),
                provider=provider, ticker_whitelist=whitelist,
            )
        except Exception as e:
            log.exception("info.classify_exception", article_id=str(art.id))
            report.articles_failed += 1
            report.errors.append(f"{art.id}: {e}")
            _persist_failure(session, "news", art.id, model_version, str(e))
            continue

        _persist_classification(session, "news", art.id, model_version, result)
        # Augment news_ticker_mentions with LLM-discovered tickers
        _augment_mentions(session, art, result.tickers_mentioned)
        report.articles_classified += 1

    log.info(
        "info.classify_done",
        seen=report.articles_seen, classified=report.articles_classified,
        failed=report.articles_failed, model=model_version,
    )
    return report


# ──────────────────────────────────────────────────────────────────────
# Scoring loop
# ──────────────────────────────────────────────────────────────────────


@require_as_of
def score_market(
    session: Session,
    *,
    market: Market,
    as_of: datetime,
    tickers: Optional[list[str]] = None,
    lookback_days: int = 21,
    source_trust_overrides: Optional[dict[str, float]] = None,
) -> InformationScoreReport:
    report = InformationScoreReport(market=market)
    if tickers is None:
        tickers = _active_tickers(session, market)

    since = as_of - timedelta(days=lookback_days)

    for ticker in tickers:
        mentions = _gather_mentions(
            session, market=market, ticker=ticker,
            since=since, as_of=as_of,
            source_trust_overrides=source_trust_overrides,
        )
        if not mentions:
            report.tickers_skipped_no_mentions += 1
            continue
        info_score = score_information(mentions)
        report.rows_written += _persist_info_score(
            session, market, ticker, as_of, info_score, mentions,
        )
        report.tickers_processed += 1

    log.info(
        "info.score_market_done", market=market.value,
        processed=report.tickers_processed,
        skipped=report.tickers_skipped_no_mentions,
        rows=report.rows_written,
    )
    return report


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────


def _default_provider() -> LLMProvider:
    return OllamaProvider()


def _model_version_string(provider: LLMProvider) -> str:
    settings = get_settings()
    if isinstance(provider, OllamaProvider):
        return f"ollama:{provider.default_model}"
    return f"{provider.name}:default"


def _active_ticker_set(session: Session) -> set[str]:
    rows = session.execute(
        select(Security.ticker).where(Security.is_active.is_(True))
    )
    return {r[0] for r in rows}


def _active_tickers(session: Session, market: Market) -> list[str]:
    stmt = (
        select(Security.ticker)
        .where(and_(Security.market == market.value, Security.is_active.is_(True)))
        .order_by(Security.ticker)
    )
    return [r[0] for r in session.execute(stmt)]


def _select_unclassified_news(
    session: Session, *, since: datetime, model_version: str, limit: int,
) -> list[NewsArticle]:
    """Recent news without an entry in article_classifications for our model_version.

    Priority order via subquery: those WITH a rule-based ticker mention
    first, then without.
    """
    has_class = (
        select(ArticleClassificationORM.article_id)
        .where(and_(
            ArticleClassificationORM.article_kind == "news",
            ArticleClassificationORM.model_version == model_version,
        ))
        .scalar_subquery()
    )
    has_mention_subq = (
        select(NewsTickerMention.article_id)
        .scalar_subquery()
    )

    # Step 1: prioritised batch — articles with rule mentions
    with_mention = list(session.scalars(
        select(NewsArticle)
        .where(and_(
            NewsArticle.published_ts >= since,
            NewsArticle.id.in_(has_mention_subq),
            NewsArticle.id.notin_(has_class),
        ))
        .order_by(desc(NewsArticle.published_ts))
        .limit(limit)
    ))
    if len(with_mention) >= limit:
        return with_mention

    # Step 2: fill remaining quota with no-mention articles
    remaining = limit - len(with_mention)
    others = list(session.scalars(
        select(NewsArticle)
        .where(and_(
            NewsArticle.published_ts >= since,
            NewsArticle.id.notin_(has_class),
        ))
        .order_by(desc(NewsArticle.published_ts))
        .limit(remaining)
    ))
    seen_ids = {a.id for a in with_mention}
    return with_mention + [a for a in others if a.id not in seen_ids]


def _persist_classification(
    session: Session, kind: str, article_id, model_version: str, result: ClassValue,
) -> None:
    row = {
        "article_kind": kind,
        "article_id": article_id,
        "model_version": model_version,
        "classified_ts": datetime.now(UTC),
        "event_type": result.event_type.value,
        "sentiment": result.sentiment.value,
        "impact": result.impact.value,
        "horizon": result.horizon.value,
        "confidence": result.confidence,
        "summary": result.summary,
        "raw_llm_output": result.as_dict(),
        "error": None,
    }
    stmt = pg_insert(ArticleClassificationORM).values([row]).on_conflict_do_nothing(
        constraint="pk_article_classifications",
    )
    session.execute(stmt)
    session.flush()


def _persist_failure(
    session: Session, kind: str, article_id, model_version: str, error: str,
) -> None:
    row = {
        "article_kind": kind,
        "article_id": article_id,
        "model_version": model_version,
        "classified_ts": datetime.now(UTC),
        "event_type": "OTHER",
        "sentiment": "NEUTRAL",
        "impact": "LOW",
        "horizon": "SHORT_TERM",
        "confidence": 0.0,
        "summary": None,
        "raw_llm_output": None,
        "error": error[:1024],
    }
    stmt = pg_insert(ArticleClassificationORM).values([row]).on_conflict_do_nothing(
        constraint="pk_article_classifications",
    )
    session.execute(stmt)
    session.flush()


def _augment_mentions(
    session: Session, article: NewsArticle, tickers: Iterable[str],
) -> None:
    """Add LLM-discovered tickers to news_ticker_mentions if not already present."""
    rows = []
    for raw in tickers:
        market = _market_for_ticker(raw)
        if market is None:
            continue
        rows.append({
            "article_id": article.id,
            "article_published_ts": article.published_ts,
            "market": market.value,
            "ticker": raw,
            "mention_kind": "llm",
            "relevance": 0.85,
        })
    if not rows:
        return
    stmt = pg_insert(NewsTickerMention).values(rows).on_conflict_do_nothing(
        constraint="pk_news_ticker_mentions",
    )
    session.execute(stmt)
    session.flush()


def _market_for_ticker(ticker: str) -> Optional[Market]:
    """Cheap classification — 6-digit numeric → KR, uppercase letters → US."""
    if ticker.isdigit() and len(ticker) == 6:
        return Market.KR
    if ticker.replace(".", "").isalpha() and ticker.isupper():
        return Market.US
    return None


def _gather_mentions(
    session: Session, *,
    market: Market, ticker: str,
    since: datetime, as_of: datetime,
    source_trust_overrides: Optional[dict[str, float]],
) -> list[WeightedMention]:
    """Build WeightedMention list from classified news articles."""
    # Join news_ticker_mentions × news_articles × article_classifications
    stmt = (
        select(
            NewsArticle.source, NewsArticle.published_ts,
            ArticleClassificationORM.sentiment,
            ArticleClassificationORM.impact,
            ArticleClassificationORM.horizon,
            ArticleClassificationORM.confidence,
        )
        .join(NewsTickerMention,
              and_(
                  NewsTickerMention.article_id == NewsArticle.id,
                  NewsTickerMention.article_published_ts == NewsArticle.published_ts,
              ))
        .join(ArticleClassificationORM,
              and_(
                  ArticleClassificationORM.article_kind == "news",
                  ArticleClassificationORM.article_id == NewsArticle.id,
              ))
        .where(and_(
            NewsTickerMention.market == market.value,
            NewsTickerMention.ticker == ticker,
            NewsArticle.published_ts >= since,
            NewsArticle.published_ts <= as_of,
            ArticleClassificationORM.error.is_(None),
        ))
    )

    out: list[WeightedMention] = []
    for row in session.execute(stmt):
        source, published_ts, sentiment_str, impact_str, horizon_str, confidence = row
        try:
            sentiment = Sentiment(sentiment_str)
            impact = Impact(impact_str)
            horizon = Horizon(horizon_str)
        except ValueError:
            continue
        age_days = (as_of - published_ts).total_seconds() / 86400.0
        out.append(WeightedMention(
            ticker=ticker, market=market.value,
            direction=sentiment.direction,
            impact_magnitude=impact.magnitude,
            confidence=float(confidence),
            source_trust=source_trust(source, overrides=source_trust_overrides),
            time_weight=time_decay_weight(age_days, horizon),
        ))
    return out


def _persist_info_score(
    session: Session, market: Market, ticker: str, as_of: datetime,
    info_score, mentions: list[WeightedMention],
) -> int:
    payload = [{
        "computed_ts": as_of,
        "market": market.value,
        "ticker": ticker,
        "module": "I",
        "score": info_score.score,
        "confidence": info_score.confidence,
        "model_version": None,
        "inputs": {
            "n_mentions": info_score.n_mentions,
            "breakdown": info_score.breakdown,
            "top_sources": _summarise_sources(mentions),
        },
    }]
    stmt = pg_insert(ModuleScore).values(payload)
    update_cols = {
        c.name: c for c in stmt.excluded
        if c.name not in {"computed_ts", "market", "ticker", "module", "created_at"}
    }
    stmt = stmt.on_conflict_do_update(constraint="pk_module_scores", set_=update_cols)
    session.execute(stmt)
    session.flush()
    return 1


def _summarise_sources(mentions: list[WeightedMention]) -> dict[str, int]:
    """Source-level breakdown for the audit inputs JSONB. Capped to
    keep payload small."""
    counts: dict[str, int] = {}
    for m in mentions:
        # WeightedMention doesn't carry the original source name. The
        # caller could pass it in; for now we summarise by trust-bucket.
        key = f"trust_{int(m.source_trust * 10)/10:.1f}"
        counts[key] = counts.get(key, 0) + 1
    return counts
