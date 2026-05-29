"""Pure equity-curve math tests.

No SQLAlchemy / FastAPI deps — these always run.
"""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from brokers.paper_equity import (  # noqa: E402
    EquityPoint, EquitySummary, TradeRecord,
    build_equity_curve, summarize,
)


def _trade(day: date, pnl: float, hour: int = 16) -> TradeRecord:
    return TradeRecord(
        exit_ts=datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
        pnl=pnl,
    )


# ──────────────────────────────────────────────────────────────────────


class BuildEquityCurveTest(unittest.TestCase):
    def test_empty_trades_returns_empty(self):
        out = build_equity_curve([], initial_balance=100_000.0)
        self.assertEqual(out, [])

    def test_single_trade_single_point(self):
        d = date(2026, 5, 20)
        out = build_equity_curve([_trade(d, 500.0)], initial_balance=10_000.0)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].date, d)
        self.assertEqual(out[0].realized_pnl_day, 500.0)
        self.assertEqual(out[0].realized_pnl_cum, 500.0)
        self.assertEqual(out[0].equity, 10_500.0)
        self.assertEqual(out[0].drawdown, 0.0)
        self.assertEqual(out[0].trades_count_day, 1)

    def test_gap_days_carry_cumulative_forward(self):
        # Trade on day1 + day3, expect 3 points with day2 flat.
        d1, d2, d3 = date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)
        out = build_equity_curve(
            [_trade(d1, 100.0), _trade(d3, -50.0)],
            initial_balance=1000.0,
        )
        self.assertEqual([p.date for p in out], [d1, d2, d3])
        self.assertEqual(out[0].equity, 1100.0)
        # Day 2 has no trades; equity unchanged from day 1.
        self.assertEqual(out[1].realized_pnl_day, 0.0)
        self.assertEqual(out[1].equity, 1100.0)
        self.assertEqual(out[1].trades_count_day, 0)
        self.assertEqual(out[2].equity, 1050.0)

    def test_drawdown_tracked_from_peak(self):
        # Peak at day 1, drop on day 2 → drawdown > 0.
        d1, d2 = date(2026, 5, 1), date(2026, 5, 2)
        out = build_equity_curve(
            [_trade(d1, 200.0), _trade(d2, -300.0)],
            initial_balance=1000.0,
        )
        # Peak = 1200, equity_day2 = 900 → drawdown = 0.25
        self.assertEqual(out[0].drawdown, 0.0)
        self.assertAlmostEqual(out[1].drawdown, 0.25, places=4)

    def test_multiple_trades_same_day_aggregate(self):
        d = date(2026, 5, 10)
        out = build_equity_curve(
            [_trade(d, 100.0, hour=10), _trade(d, -30.0, hour=15)],
            initial_balance=1000.0,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].realized_pnl_day, 70.0)
        self.assertEqual(out[0].trades_count_day, 2)

    def test_window_clipping(self):
        # Trades span 3 days; restrict to middle day only.
        d1 = date(2026, 5, 1)
        d2 = date(2026, 5, 2)
        d3 = date(2026, 5, 3)
        out = build_equity_curve(
            [_trade(d1, 100.0), _trade(d2, 50.0), _trade(d3, -20.0)],
            initial_balance=1000.0,
            start=d2, end=d2,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].date, d2)
        # Only the d2 trade counted in cum (math is local to window)
        self.assertEqual(out[0].realized_pnl_cum, 50.0)


class SummarizeTest(unittest.TestCase):
    def test_no_trades_returns_zeros(self):
        out = summarize([], [], initial_balance=1000.0)
        self.assertEqual(out.total_trades, 0)
        self.assertEqual(out.win_rate, 0.0)
        self.assertEqual(out.return_pct, 0.0)

    def test_win_rate_and_avgs(self):
        d1, d2, d3 = date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)
        trades = [_trade(d1, 100.0), _trade(d2, -40.0), _trade(d3, 60.0)]
        curve = build_equity_curve(trades, initial_balance=1000.0)
        s = summarize(trades, curve, initial_balance=1000.0)
        self.assertEqual(s.total_trades, 3)
        self.assertEqual(s.wins, 2)
        self.assertEqual(s.losses, 1)
        self.assertAlmostEqual(s.win_rate, 2 / 3)
        self.assertEqual(s.total_realized_pnl, 120.0)
        self.assertAlmostEqual(s.avg_trade_pnl, 40.0)
        self.assertEqual(s.best_trade_pnl, 100.0)
        self.assertEqual(s.worst_trade_pnl, -40.0)
        # Return = 1120/1000 - 1 = 0.12
        self.assertAlmostEqual(s.return_pct, 0.12, places=4)

    def test_max_drawdown_picks_worst(self):
        d1, d2, d3 = date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)
        trades = [_trade(d1, 500.0), _trade(d2, -300.0), _trade(d3, 100.0)]
        curve = build_equity_curve(trades, initial_balance=1000.0)
        s = summarize(trades, curve, initial_balance=1000.0)
        # Peak = 1500 (day 1), low = 1200 (day 2). Max DD = 300/1500 = 0.2
        self.assertAlmostEqual(s.max_drawdown, 0.2, places=4)

    def test_zero_volatility_sharpe_is_zero(self):
        # All gains identical → daily returns degenerate to a constant
        # (well, not constant due to compounding base shifting), but
        # at the very least no division by zero crash.
        d1, d2 = date(2026, 5, 1), date(2026, 5, 2)
        trades = [_trade(d1, 0.0), _trade(d2, 0.0)]
        curve = build_equity_curve(trades, initial_balance=1000.0)
        s = summarize(trades, curve, initial_balance=1000.0)
        self.assertEqual(s.sharpe_like, 0.0)


class RiskAdjustedMetricsTest(unittest.TestCase):
    def test_profit_factor_and_expectancy(self):
        # 3 wins @ +100, 2 losses @ -50 → gross W=300, gross L=100
        # profit_factor = 3.0, win_rate = 0.6
        # avg_win = 100, avg_loss = 50 → expectancy = 0.6*100 - 0.4*50 = 40
        d = date(2026, 5, 1)
        trades = (
            [_trade(d, 100.0)] * 3
            + [_trade(date(2026, 5, 2), -50.0)] * 2
        )
        curve = build_equity_curve(trades, initial_balance=1000.0)
        s = summarize(trades, curve, initial_balance=1000.0)
        self.assertAlmostEqual(s.profit_factor, 3.0, places=4)
        self.assertAlmostEqual(s.expectancy, 40.0, places=4)

    def test_profit_factor_zero_when_no_losses(self):
        d = date(2026, 5, 1)
        trades = [_trade(d, 100.0), _trade(date(2026, 5, 2), 50.0)]
        curve = build_equity_curve(trades, initial_balance=1000.0)
        s = summarize(trades, curve, initial_balance=1000.0)
        # No losses → can't compute ratio safely; spec returns 0.0
        self.assertEqual(s.profit_factor, 0.0)

    def test_sortino_higher_than_sharpe_when_downside_smaller(self):
        # Construct a curve where downside vol is genuinely smaller than
        # total vol → Sortino should exceed Sharpe.
        # Gains: +5, +5, +5, +5. Loss: -1. Total stdev > downside stdev.
        trades = [
            _trade(date(2026, 5, d), p)
            for d, p in zip(range(1, 6), [5, 5, 5, 5, -1])
        ]
        curve = build_equity_curve(trades, initial_balance=1000.0)
        s = summarize(trades, curve, initial_balance=1000.0)
        self.assertGreater(s.sortino_like, s.sharpe_like)

    def test_sortino_caps_at_99_when_no_losses_with_positive_mean(self):
        trades = [_trade(date(2026, 5, d), 5.0) for d in range(1, 5)]
        curve = build_equity_curve(trades, initial_balance=1000.0)
        s = summarize(trades, curve, initial_balance=1000.0)
        # No losing day + positive mean → sortino reported as the 99 cap
        # rather than infinity (UI table would break).
        self.assertEqual(s.sortino_like, 99.0)

    def test_calmar_zero_when_no_drawdown(self):
        # Strictly monotone-up equity → drawdown=0 → Calmar undefined → 0
        trades = [_trade(date(2026, 5, d), 5.0) for d in range(1, 5)]
        curve = build_equity_curve(trades, initial_balance=1000.0)
        s = summarize(trades, curve, initial_balance=1000.0)
        self.assertEqual(s.calmar, 0.0)

    def test_calmar_positive_when_recovers_above_drawdown(self):
        # Up 200, down 100, up 100 → final equity gain 200, max DD 100/1200
        d1, d2, d3 = date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)
        trades = [_trade(d1, 200.0), _trade(d2, -100.0), _trade(d3, 100.0)]
        curve = build_equity_curve(trades, initial_balance=1000.0)
        s = summarize(trades, curve, initial_balance=1000.0)
        self.assertGreater(s.calmar, 0.0)
        self.assertGreater(s.max_drawdown, 0.0)


if __name__ == "__main__":
    unittest.main()
