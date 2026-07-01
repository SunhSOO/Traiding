"""Paper-route tests with mocked AsyncSession.

We exercise the response composition + filtering. Real DB queries
land in tests/integration once PostgreSQL is up."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
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

    from core.models.paper import PaperAccount, PaperPosition, PaperTrade  # noqa: E402
    from routes.paper import account_snapshot, positions, top_movers, trades  # noqa: E402


def _mock_session(plan):
    """Build a session whose `execute(stmt)` resolves to canned
    response objects in order. Each plan entry is a callable that
    receives the str(stmt) and returns the MagicMock to return."""
    session = MagicMock()
    session.get = AsyncMock(return_value=None)

    state = {"i": 0}
    async def _execute(stmt):
        idx = state["i"]
        state["i"] += 1
        if idx < len(plan):
            entry = plan[idx]
            return entry(str(stmt))
        return MagicMock()
    session.execute = AsyncMock(side_effect=_execute)
    return session


def _scalars_first(value):
    r = MagicMock()
    sc = MagicMock()
    sc.first = MagicMock(return_value=value)
    sc.__iter__ = lambda self_: iter([value] if value else [])
    r.scalars = MagicMock(return_value=sc)
    return r


def _scalar_value(value):
    r = MagicMock()
    r.scalar = MagicMock(return_value=value)
    return r


def _all_value(value):
    r = MagicMock()
    r.all = MagicMock(return_value=value)
    return r


def _scalars_iter(values):
    r = MagicMock()
    sc = MagicMock()
    sc.__iter__ = lambda self_: iter(values)
    r.scalars = MagicMock(return_value=sc)
    return r


# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "fastapi/sqlalchemy not installed")
class AccountSnapshotTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_zero_when_account_missing(self):
        session = _mock_session([
            lambda _: _scalars_first(None),     # PaperAccount lookup
        ])
        out = await account_snapshot(user=None, db=session, name="default", market=None)
        self.assertEqual(out.id, 0)
        self.assertEqual(out.current_balance, 0.0)
        self.assertEqual(out.open_positions, 0)

    async def test_returns_aggregates(self):
        acc = PaperAccount(id=7, name="default", base_currency="USD",
                           initial_balance=10000.0, current_balance=10500.0)
        session = _mock_session([
            lambda _: _scalars_first(acc),                # PaperAccount
            lambda _: _all_value([("KR", 2), ("US", 3)]), # counts
            lambda _: _scalar_value(42),                  # closed_count
            lambda _: _scalar_value(1234.5),              # realised_total
            lambda _: _scalar_value(20.0),                # realised_today
        ])
        out = await account_snapshot(user=None, db=session, name="default", market=None)
        self.assertEqual(out.id, 7)
        self.assertEqual(out.open_positions, 5)
        self.assertEqual(out.open_positions_kr, 2)
        self.assertEqual(out.open_positions_us, 3)
        self.assertEqual(out.closed_trades_total, 42)
        self.assertAlmostEqual(out.realised_pnl_total, 1234.5)
        self.assertAlmostEqual(out.realised_pnl_today, 20.0)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class PositionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_account(self):
        session = _mock_session([
            lambda _: _scalars_first(None),
        ])
        out = await positions(user=None, db=session)
        self.assertEqual(out, [])

    async def test_invalid_market_raises_400(self):
        acc = PaperAccount(id=1, name="default", base_currency="USD",
                           initial_balance=0, current_balance=0)
        session = _mock_session([lambda _: _scalars_first(acc)])
        with self.assertRaises(Exception) as cm:
            await positions(user=None, db=session, market="XX")
        self.assertIn("unknown market", str(cm.exception).lower())

    async def test_basic_position_with_unrealised_pnl(self):
        acc = PaperAccount(id=1, name="default", base_currency="USD",
                           initial_balance=10000, current_balance=10000)
        pos = PaperPosition(
            account_id=1, market="US", ticker="AAPL", side="BUY",
            volume=10, entry_price=150.0, entry_ts=datetime.now(UTC),
        )

        async def _exec(stmt):
            s = str(stmt).lower()
            if "paper_accounts" in s:
                return _scalars_first(acc)
            if "paper_positions" in s and "select " in s:
                return _scalars_iter([pos])
            if "daily_prices" in s:
                # `_latest_prices` now issues ONE statement whose SQL embeds
                # the `max(trade_date)` subquery *and* selects the close, so
                # match on daily_prices alone and return the price rows.
                r = MagicMock()
                r.__iter__ = lambda self_: iter([("US", "AAPL", 165.0)])
                return r
            if "securities" in s:
                r = MagicMock()
                r.__iter__ = lambda self_: iter([("US", "AAPL", "Apple Inc.")])
                return r
            return MagicMock()

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_exec)
        out = await positions(user=None, db=session, market="US", account_name=None)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].ticker, "AAPL")
        # Long bought at 150, now 165 → +150 unrealised on 10 shares
        self.assertAlmostEqual(out[0].unrealised_pnl, 150.0, places=2)
        # 150 / (150*10) * 100 = 10%
        self.assertAlmostEqual(out[0].unrealised_pnl_pct, 10.0, places=2)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class TradesTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_account(self):
        session = _mock_session([lambda _: _scalars_first(None)])
        out = await trades(user=None, db=session)
        self.assertEqual(out, [])

    async def test_basic_trade_with_pct(self):
        acc = PaperAccount(id=1, name="default", base_currency="KRW",
                           initial_balance=0, current_balance=0)
        trade = PaperTrade(
            id=1, account_id=1, market="KR", ticker="005930", side="BUY",
            volume=10, entry_price=70_000, exit_price=72_000,
            entry_ts=datetime.now(UTC), exit_ts=datetime.now(UTC),
            pnl=20_000.0, commission=10.0, tax=0.0,
        )

        async def _exec(stmt):
            s = str(stmt).lower()
            if "paper_accounts" in s:
                return _scalars_first(acc)
            return _scalars_iter([trade])
        session = MagicMock()
        session.execute = AsyncMock(side_effect=_exec)
        out = await trades(user=None, db=session, market="KR", limit=100)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0].pnl, 20_000.0)
        # gross = 70000 * 10 = 700000 → pct = 20000 / 700000 * 100 ≈ 2.857
        self.assertAlmostEqual(out[0].pnl_pct, 20_000.0 / 700_000.0 * 100.0, places=3)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class TopMoversTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_scores(self):
        async def _exec(stmt):
            return _scalars_iter([])
        session = MagicMock()
        session.execute = AsyncMock(side_effect=_exec)
        out = await top_movers(user=None, db=session, market="KR", limit=5, direction="both")
        self.assertEqual(out, [])

    async def test_invalid_market_raises(self):
        session = MagicMock()
        with self.assertRaises(Exception):
            await top_movers(user=None, db=session, market="XX", limit=5, direction="both")


if __name__ == "__main__":
    unittest.main()
