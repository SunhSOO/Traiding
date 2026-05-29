"""Equity-curve route shape test.

Skipped when SQLAlchemy / FastAPI aren't installed (graceful-skip
pattern). Pins response composition only — pure-math correctness is
in ``test_paper_equity.py``.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-please-replace-with-32-random-bytes-x")

try:
    import fastapi  # noqa: F401
    import sqlalchemy  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from core.config import reset_settings_cache  # noqa: E402

    reset_settings_cache()

    from routes.paper import equity_curve  # noqa: E402


def _account(name="default", base_currency="USD", initial=10_000.0):
    return SimpleNamespace(
        id=1, name=name, base_currency=base_currency,
        initial_balance=initial, current_balance=initial,
    )


def _build_session(*, account, trade_rows, position_rows, price_rows):
    """Build an AsyncSession that returns canned data based on the
    SQL stringification (cheap-and-cheerful pattern used by sibling
    tests)."""
    session = MagicMock()
    calls = {"account_first": False}

    async def _execute(stmt):
        s = str(stmt).lower()
        r = MagicMock()
        if not calls["account_first"]:
            calls["account_first"] = True
            scalars = MagicMock()
            scalars.first = MagicMock(return_value=account)
            r.scalars = MagicMock(return_value=scalars)
            return r
        if "paper_trades" in s:
            r.all = MagicMock(return_value=trade_rows)
            return r
        if "paper_positions" in s:
            scalars = MagicMock()
            scalars.__iter__ = MagicMock(return_value=iter(position_rows))
            r.scalars = MagicMock(return_value=scalars)
            return r
        if "daily_prices" in s:
            r.__iter__ = MagicMock(return_value=iter(price_rows))
            return r
        r.all = MagicMock(return_value=[])
        scalars = MagicMock()
        scalars.first = MagicMock(return_value=None)
        scalars.__iter__ = MagicMock(return_value=iter([]))
        r.scalars = MagicMock(return_value=scalars)
        return r

    session.execute = AsyncMock(side_effect=_execute)
    return session


# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps")
class EquityCurveRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_account_returns_empty_curve(self):
        session = MagicMock()

        async def _execute(stmt):
            r = MagicMock()
            scalars = MagicMock()
            scalars.first = MagicMock(return_value=None)
            scalars.__iter__ = MagicMock(return_value=iter([]))
            r.scalars = MagicMock(return_value=scalars)
            r.all = MagicMock(return_value=[])
            r.__iter__ = MagicMock(return_value=iter([]))
            return r

        session.execute = AsyncMock(side_effect=_execute)
        out = await equity_curve(
            user=None, db=session, account_name="ghost", days=30,
        )
        self.assertEqual(out.account_name, "ghost")
        self.assertEqual(out.points, [])
        self.assertEqual(out.summary.total_trades, 0)
        self.assertEqual(out.current_equity, 0.0)

    async def test_basic_curve_with_trades_no_open(self):
        d1 = datetime(2026, 5, 20, 16, tzinfo=UTC)
        d2 = datetime(2026, 5, 21, 16, tzinfo=UTC)
        trade_rows = [(d1, 250.0), (d2, -100.0)]
        session = _build_session(
            account=_account(initial=10_000.0),
            trade_rows=trade_rows,
            position_rows=[],
            price_rows=[],
        )
        out = await equity_curve(
            user=None, db=session, account_name="default", days=30,
        )
        self.assertEqual(out.account_name, "default")
        self.assertEqual(out.base_currency, "USD")
        self.assertEqual(out.summary.total_trades, 2)
        self.assertEqual(out.summary.wins, 1)
        self.assertEqual(out.summary.losses, 1)
        self.assertAlmostEqual(out.summary.total_realized_pnl, 150.0)
        # No open positions → unrealised P&L is 0.
        self.assertEqual(out.unrealised_pnl, 0.0)
        self.assertEqual(out.open_positions, 0)
        # Current equity = initial + cum realised + 0 unrealised
        self.assertAlmostEqual(out.current_equity, 10_150.0)
        # Two distinct trade days → curve has 2 points
        self.assertEqual(len(out.points), 2)
        self.assertEqual(out.points[0].equity, 10_250.0)
        self.assertEqual(out.points[-1].equity, 10_150.0)


if __name__ == "__main__":
    unittest.main()
