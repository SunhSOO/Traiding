"""DB-backed :class:`PriceOracle` implementation.

Quotes are derived from the most-recent ``daily_prices`` row for
each (market, ticker). Since daily bars don't carry bid/ask, we
synthesise a symmetric ±0.5 bp spread around the close — enough for
the paper broker's fill model to behave realistically without
demanding tick data we don't ingest.

For backtests / look-ahead-safe replays, callers should set the
``as_of`` cap explicitly so the oracle never reads bars filed after
the reference instant.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from brokers.paper import Quote
from core.logging import get_logger
from core.models.prices import DailyPrice
from core.types import Market

log = get_logger(__name__)

DEFAULT_SYNTHETIC_HALF_SPREAD_BPS = 0.5


@dataclass
class DBPriceOracle:
    """A PriceOracle that reads from ``daily_prices`` via the injected
    session factory. The factory is called per quote so callers don't
    have to manage session lifetime.

    Parameters
    ----------
    session_factory : callable
        Returns a SQLAlchemy ``Session`` when called (e.g.
        ``core.db.session_scope`` consumed via ``with`` — but we
        ourselves call it as a plain callable). Tests pass an in-memory
        stub.
    half_spread_bps : float
        Half-spread to synthesise around the close. 0.5 bps ≈ 0.005%.
    """

    session_factory: callable
    half_spread_bps: float = DEFAULT_SYNTHETIC_HALF_SPREAD_BPS

    def quote(self, market: Market, ticker: str, *, as_of: datetime) -> Quote:
        with self.session_factory() as session:
            row = self._latest_price(session, market, ticker, as_of)
        if row is None:
            raise LookupError(
                f"No price for {market.value}:{ticker} available at "
                f"{as_of.isoformat()} (DailyPrice empty)"
            )
        close = float(row.close)
        bp = self.half_spread_bps / 10_000.0
        bid = close * (1 - bp)
        ask = close * (1 + bp)
        return Quote(bid=bid, ask=ask, last=close, ts=as_of)

    @staticmethod
    def _latest_price(
        session: Session, market: Market, ticker: str, as_of: datetime,
    ) -> Optional[DailyPrice]:
        stmt = (
            select(DailyPrice)
            .where(and_(
                DailyPrice.market == market.value,
                DailyPrice.ticker == ticker,
                DailyPrice.as_of_ts <= as_of,
            ))
            .order_by(desc(DailyPrice.trade_date))
            .limit(1)
        )
        return session.scalars(stmt).first()
