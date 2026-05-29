"""Technical runner — pull daily prices, score one ticker, persist.

Flow:
1. Load last N (default 300) daily bars for one (market, ticker) from
   ``daily_prices`` filtered by ``as_of`` (look-ahead guard).
2. Build IndicatorContext from those bars.
3. Run ``score_technical`` → TechnicalScore.
4. Upsert into ``module_scores`` with module='T'.

Bulk variant runs over every ticker in a market. Each ticker is
independent so failures don't block others.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.as_of import require_as_of
from core.logging import get_logger
from core.models.prices import DailyPrice
from core.models.scores import ModuleScore
from core.models.universe import Security
from core.types import Market
from technical.indicators import from_ohlcv
from technical.score import TechnicalScore, score_technical

log = get_logger(__name__)


@dataclass
class TechnicalRunReport:
    market: Market
    tickers_processed: int = 0
    tickers_failed: int = 0
    tickers_abstained: int = 0
    rows_written: int = 0
    errors: list[str] = field(default_factory=list)


@require_as_of
def score_one_ticker(
    session: Session,
    *,
    market: Market,
    ticker: str,
    as_of: datetime,
    lookback_bars: int = 300,
) -> Optional[TechnicalScore]:
    """Score one ticker as-of ``as_of``. Returns None if there aren't
    enough bars yet."""
    rows = _load_bars(session, market, ticker, as_of, lookback_bars)
    if len(rows) < 30:
        return None
    ctx = from_ohlcv(
        opens=[float(r.open) for r in rows],
        highs=[float(r.high) for r in rows],
        lows=[float(r.low) for r in rows],
        closes=[float(r.close) for r in rows],
        volumes=[float(r.volume) for r in rows],
    )
    return score_technical(ctx)


def score_market(
    session: Session,
    *,
    market: Market,
    as_of: datetime,
    tickers: Optional[list[str]] = None,
    lookback_bars: int = 300,
) -> TechnicalRunReport:
    """Score every (or selected) ticker in ``market`` and persist."""
    report = TechnicalRunReport(market=market)
    if tickers is None:
        tickers = _active_tickers(session, market)

    for ticker in tickers:
        try:
            ts = score_one_ticker(
                session, market=market, ticker=ticker,
                as_of=as_of, lookback_bars=lookback_bars,
            )
        except Exception as e:
            log.exception("technical.score_failed", market=market.value, ticker=ticker)
            report.tickers_failed += 1
            report.errors.append(f"{ticker}: {e}")
            continue

        if ts is None:
            report.tickers_abstained += 1
            continue

        report.tickers_processed += 1
        report.rows_written += _persist(session, market, ticker, as_of, ts)

    log.info(
        "technical.run_done", market=market.value,
        processed=report.tickers_processed, failed=report.tickers_failed,
        abstained=report.tickers_abstained, rows=report.rows_written,
    )
    return report


# ──────────────────────────────────────────────────────────────────────


def _persist(
    session: Session,
    market: Market,
    ticker: str,
    as_of: datetime,
    ts: TechnicalScore,
) -> int:
    payload = [{
        "computed_ts": as_of,
        "market": market.value,
        "ticker": ticker,
        "module": "T",
        "score": ts.score,
        "confidence": ts.confidence,
        "model_version": None,
        "inputs": _truncate_inputs(ts.as_dict()),
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


def _truncate_inputs(inputs: dict) -> dict:
    """Cap stored inputs at ~4KB to keep the JSONB column light. We
    keep the top-level shape and per-signal score/confidence; drop
    any signal-internal ``inputs`` sub-dict over 1024 chars."""
    import json
    out = dict(inputs)
    for sig in out.get("signals", []):
        sub = sig.get("inputs")
        if sub is not None and len(json.dumps(sub, default=str)) > 1024:
            sig["inputs"] = {"truncated": True}
    return out


def _active_tickers(session: Session, market: Market) -> list[str]:
    stmt = (
        select(Security.ticker)
        .where(and_(Security.market == market.value, Security.is_active.is_(True)))
        .order_by(Security.ticker)
    )
    return [r[0] for r in session.execute(stmt)]


def _load_bars(
    session: Session,
    market: Market,
    ticker: str,
    as_of: datetime,
    lookback: int,
) -> list[DailyPrice]:
    """Pull last ``lookback`` bars where as_of_ts <= as_of (look-ahead guard)."""
    stmt = (
        select(DailyPrice)
        .where(and_(
            DailyPrice.market == market.value,
            DailyPrice.ticker == ticker,
            DailyPrice.as_of_ts <= as_of,
        ))
        .order_by(DailyPrice.trade_date.desc())
        .limit(lookback)
    )
    rows = list(session.scalars(stmt))
    rows.reverse()  # ascending by date
    return rows
