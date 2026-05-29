"""Point-in-time universe membership queries.

The single most important rule for any historical replay or
re-scoring is: a ticker that was NOT a member of the target index on
date T must not be allowed to trade on date T. Without this filter
backtests acquire survivorship bias (we know which tickers happened
to stay listed; we used to know that prospectively).

``UniverseMembership`` rows already carry ``valid_from`` /
``valid_to``. This module wraps that history into pure-function
queries the backtest / rescoring layers can call without re-deriving
the SQL each time.

Two patterns:

- :func:`members_at_date` — single point-in-time fetch. Cheap one-off
  SQL; good for "what was in KOSPI200 on 2023-04-15?".

- :class:`MembershipFilter` — pre-loads the whole window once and
  answers "was X a member on date D?" in O(1). Use this when the
  caller is going to ask the same question for many (ticker, date)
  pairs during a multi-month replay."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType
from typing import Iterable, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from core.models.prices import UniverseMembership


def members_at_date(
    session: Session,
    *,
    market: str,
    index_code: str,
    on: DateType,
) -> set[str]:
    """Return the set of tickers in ``market``/``index_code`` on ``on``.

    A row counts if ``valid_from <= on`` AND (``valid_to`` is NULL OR
    ``valid_to`` > on)."""
    stmt = (
        select(UniverseMembership.ticker)
        .where(and_(
            UniverseMembership.market == market,
            UniverseMembership.index_code == index_code,
            UniverseMembership.valid_from <= on,
            or_(
                UniverseMembership.valid_to.is_(None),
                UniverseMembership.valid_to > on,
            ),
        ))
    )
    return set(session.scalars(stmt).all())


@dataclass
class MembershipFilter:
    """O(1) membership lookup for backtest hot paths.

    Loads every membership row for ``(market, index_codes)`` once,
    then :meth:`is_member` answers in memory by walking each ticker's
    history. Build once at the top of a replay; reuse for every
    decision row."""

    # ticker → list of (valid_from, valid_to) ranges
    _ranges: dict[str, list[tuple[DateType, Optional[DateType]]]]

    @classmethod
    def from_db(
        cls,
        session: Session,
        *,
        market: str,
        index_codes: Iterable[str],
    ) -> "MembershipFilter":
        codes = list(index_codes)
        if not codes:
            return cls(_ranges={})
        rows = list(session.execute(
            select(
                UniverseMembership.ticker,
                UniverseMembership.valid_from,
                UniverseMembership.valid_to,
            ).where(and_(
                UniverseMembership.market == market,
                UniverseMembership.index_code.in_(codes),
            ))
        ).all())
        out: dict[str, list[tuple[DateType, Optional[DateType]]]] = {}
        for ticker, vf, vt in rows:
            out.setdefault(ticker, []).append((vf, vt))
        return cls(_ranges=out)

    def is_member(self, ticker: str, on: DateType) -> bool:
        ranges = self._ranges.get(ticker)
        if not ranges:
            return False
        for valid_from, valid_to in ranges:
            if valid_from <= on and (valid_to is None or valid_to > on):
                return True
        return False

    def members_at(self, on: DateType) -> set[str]:
        return {t for t in self._ranges if self.is_member(t, on)}
