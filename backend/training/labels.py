"""Label generation — forward returns paired with module scores.

For each (ticker, score_ts) tuple emitted by the F/T/I modules, the
supervised label is::

    target = close[score_ts + horizon] / close[score_ts] - 1

The look-ahead guard is honoured: scores are read at their own
``computed_ts`` (which already filtered for ``as_of <= score_ts``),
and the price-at-T is looked up via the ``as_of`` of the daily_prices
table.

We **do not** average future returns over multiple horizons; the
caller chooses ``horizon_days`` once per training run. For a multi-
horizon backtest, run the trainer multiple times.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from core.as_of import require_as_of
from core.models.prices import DailyPrice
from core.models.scores import ModuleScore
from training.types import TrainSample


@require_as_of
def forward_return_label(
    session: Session,
    *,
    market: str,
    ticker: str,
    as_of: datetime,
    horizon_days: int,
) -> Optional[float]:
    """Return (price[as_of + horizon] / price[as_of] - 1), or None if
    either anchor is missing."""
    anchor = _price_on_or_before(session, market, ticker, as_of)
    if anchor is None:
        return None
    future_cutoff = as_of + timedelta(days=horizon_days)
    future = _price_on_or_before(session, market, ticker, future_cutoff)
    if future is None or future is anchor:
        return None
    base = float(anchor.close)
    if base <= 0:
        return None
    return float(future.close) / base - 1.0


def label_pairs(
    session: Session,
    *,
    market: str,
    tickers: Iterable[str],
    score_window_start: datetime,
    score_window_end: datetime,
    horizon_days: int,
    min_confidence: float = 0.2,
) -> list[TrainSample]:
    """Walk through ``module_scores`` in [start, end) for each ticker
    and build paired TrainSample rows.

    A "score event" is a single ``score_ts`` where all three modules
    (F, T, I) have a row at the SAME timestamp. If only some modules
    fire at a timestamp, the missing modules contribute 0 score with 0
    confidence — that lets the trainer learn that "only F speaking"
    samples are noisy.
    """
    samples: list[TrainSample] = []
    for ticker in tickers:
        ticker_samples = _collect_for_ticker(
            session, market=market, ticker=ticker,
            start=score_window_start, end=score_window_end,
            horizon_days=horizon_days, min_confidence=min_confidence,
        )
        samples.extend(ticker_samples)
    return samples


def _collect_for_ticker(
    session: Session, *,
    market: str, ticker: str,
    start: datetime, end: datetime,
    horizon_days: int, min_confidence: float,
) -> list[TrainSample]:
    """Group module_scores by timestamp; pair with forward return."""
    stmt = (
        select(ModuleScore)
        .where(and_(
            ModuleScore.market == market,
            ModuleScore.ticker == ticker,
            ModuleScore.computed_ts >= start,
            ModuleScore.computed_ts < end,
        ))
        .order_by(ModuleScore.computed_ts)
    )
    rows = list(session.scalars(stmt))

    # Group by computed_ts (truncated to second)
    by_ts: dict[datetime, dict[str, ModuleScore]] = {}
    for r in rows:
        # Bucket scores within the same second together
        key = r.computed_ts.replace(microsecond=0)
        by_ts.setdefault(key, {})[r.module] = r

    out: list[TrainSample] = []
    for ts, modules in by_ts.items():
        # Require at least one module above min_confidence
        confidences = [float(m.confidence) for m in modules.values()]
        if not confidences or max(confidences) < min_confidence:
            continue

        target = forward_return_label(
            session, market=market, ticker=ticker,
            as_of=ts, horizon_days=horizon_days,
        )
        if target is None:
            continue

        def get(code, key, default=0.0):
            row = modules.get(code)
            if row is None:
                return default
            return float(getattr(row, key))

        out.append(TrainSample(
            market=market, ticker=ticker, score_ts=ts,
            f_score=get("F", "score"),
            t_score=get("T", "score"),
            i_score=get("I", "score"),
            f_confidence=get("F", "confidence"),
            t_confidence=get("T", "confidence"),
            i_confidence=get("I", "confidence"),
            target=target,
        ))

    return out


def _price_on_or_before(
    session: Session, market: str, ticker: str, ts: datetime,
) -> Optional[DailyPrice]:
    """Latest DailyPrice with trade_date.as_of_ts <= ts (look-ahead safe)."""
    stmt = (
        select(DailyPrice)
        .where(and_(
            DailyPrice.market == market,
            DailyPrice.ticker == ticker,
            DailyPrice.as_of_ts <= ts,
        ))
        .order_by(desc(DailyPrice.trade_date))
        .limit(1)
    )
    return session.scalars(stmt).first()
