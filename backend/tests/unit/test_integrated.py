"""Integrated pipeline unit tests — selection/market-read math + timing gates.

Covers the pure decision logic of the selection→execution pipeline:
  * regime → base exposure mapping
  * run_selection breadth / target_exposure / basket weight computation
  * technical-timing threshold sanity (D1: timing only)
Full DB+broker execution is covered by the integration smoke (run_integrated_job).
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import pandas as pd
    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


@unittest.skipUnless(_HAVE_DEPS, "pandas required")
class TestExposureMapping(unittest.TestCase):
    def test_base_exposure_by_regime(self):
        from decision.selection import _base_exposure
        self.assertEqual(_base_exposure("crisis"), 0.0)
        self.assertEqual(_base_exposure("risk_off"), 0.4)
        self.assertEqual(_base_exposure("neutral"), 0.7)
        self.assertEqual(_base_exposure("risk_on"), 1.0)
        self.assertEqual(_base_exposure("calm_bull"), 1.0)
        self.assertEqual(_base_exposure("RISK_ON"), 1.0)        # case-insensitive
        self.assertEqual(_base_exposure(None), 0.7)             # default
        self.assertEqual(_base_exposure("weird_label"), 0.7)    # unknown → default


@unittest.skipUnless(_HAVE_DEPS, "pandas required")
class TestRunSelection(unittest.TestCase):
    def _recs(self):
        # 10 names: 6 with positive pred_ret (breadth 0.6); 3 are BUY (top decile)
        rows = []
        for i in range(10):
            rows.append({
                "ticker": f"T{i}", "rank_pct": 0.95 - i * 0.1,
                "pred_ret": 0.05 if i < 6 else -0.02,
                "target_price": 100 + i, "band_low": 90 + i, "band_high": 110 + i,
                "action": "BUY" if i < 3 else ("SELL" if i >= 9 else "HOLD"),
            })
        return pd.DataFrame(rows)

    def _session(self, regime="neutral", conf=0.6):
        s = MagicMock()
        s.execute.return_value.first.return_value = (regime, conf)
        return s

    def test_breadth_exposure_basket(self):
        from decision.selection import run_selection
        res = run_selection(self._session("neutral"), market="US",
                            as_of=datetime(2026, 6, 29), recs=self._recs(), persist=False)
        self.assertAlmostEqual(res.breadth, 0.6, places=3)        # 6/10 positive
        # neutral base 0.7 × min(1, 0.4+0.6)=1.0 → 0.7
        self.assertAlmostEqual(res.target_exposure, 0.7, places=3)
        self.assertEqual(len(res.basket), 3)                       # 3 BUYs
        self.assertAlmostEqual(res.basket[0].target_weight, 1 / 3, places=4)
        # conviction = mean predicted 21d return of the 3 BUYs (all 0.05)
        self.assertAlmostEqual(res.avg_conviction, 0.05, places=4)

    def test_crisis_zero_exposure(self):
        from decision.selection import run_selection
        res = run_selection(self._session("crisis"), market="US",
                            as_of=datetime(2026, 6, 29), recs=self._recs(), persist=False)
        self.assertEqual(res.target_exposure, 0.0)                 # crisis → no exposure

    def test_price_breadth_sma50_primary(self):
        # Exposure breadth = calibration-free PRICE breadth (SMA50 preferred over
        # the slow SMA200), NOT blended with the model's pred_breadth optimism.
        from decision.selection import run_selection
        recs = self._recs()                                        # pred_breadth 0.6 (ignored)
        feat = pd.DataFrame({"ticker": [f"T{i}" for i in range(10)],
                             "px_vs_sma50": [0.1] * 7 + [-0.1] * 3,    # SMA50 breadth 0.7
                             "px_vs_sma200": [0.1] * 10})              # SMA200 1.0 — must be ignored
        res = run_selection(self._session("neutral"), market="US",
                            as_of=datetime(2026, 6, 29), recs=recs, feat_df=feat, persist=False)
        self.assertAlmostEqual(res.breadth, 0.7, places=3)         # SMA50 price breadth drives it

    def test_thin_breadth_reduces_exposure(self):
        from decision.selection import run_selection
        recs = self._recs()
        recs["pred_ret"] = -0.01                                   # breadth 0 (all negative)
        res = run_selection(self._session("neutral"), market="US",
                            as_of=datetime(2026, 6, 29), recs=recs, persist=False)
        # 0.7 × min(1, 0.4+0.0) = 0.28
        self.assertAlmostEqual(res.target_exposure, 0.28, places=3)


@unittest.skipUnless(_HAVE_DEPS, "pandas required")
class TestMarketDirection(unittest.TestCase):
    def test_build_market_frame_one_row_per_date(self):
        from decision.market_direction import build_market_frame
        df = pd.DataFrame({
            "date": ["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"],
            "ticker": ["A", "B", "A", "B"],
            "ret_fwd_21d": [0.01, 0.03, -0.01, 0.05],
            "px_vs_sma200": [0.1, -0.1, 0.2, 0.2], "vix": [15, 15, 16, 16],
            "ret_21d": [0.02, 0.01, -0.01, 0.0],
        })
        mf = build_market_frame(df)
        self.assertEqual(len(mf), 2)                        # one row per date
        self.assertAlmostEqual(mf.iloc[0]["fwd"], 0.02)     # mean(0.01,0.03)
        self.assertAlmostEqual(mf.iloc[0]["px_vs_sma200_frac"], 0.5)  # 1 of 2 > 0

    def test_predict_direction_none_when_short(self):
        from decision.market_direction import predict_direction
        df = pd.DataFrame({"date": ["2024-01-02"], "ticker": ["A"],
                           "ret_fwd_21d": [0.01], "vix": [15]})
        self.assertIsNone(predict_direction(df))           # nowhere near min_train


@unittest.skipUnless(_HAVE_DEPS, "pandas required")
class TestTimingThresholds(unittest.TestCase):
    def test_timing_constants_sane(self):
        from decision.integrated_runner import ENTRY_MIN, EXIT_MAX, MIN_EXPOSURE
        # exit threshold must be below entry threshold (hysteresis); D1 timing-only
        self.assertLess(EXIT_MAX, ENTRY_MIN)
        self.assertGreater(MIN_EXPOSURE, 0.0)
        self.assertLess(MIN_EXPOSURE, 0.1)


@unittest.skipUnless(_HAVE_DEPS, "pandas required")
class TestIntegratedRunner(unittest.TestCase):
    """Runner decision logic (C+D) with run_selection + technical timing +
    broker patched out, so we assert BUY/WAIT/SELL/REJECTED orchestration."""

    def _run(self, *, basket, tech_scores, positions=None, exposure=0.7,
             risk_ok=True, concentrate=False, rebalance_band=None, direction_score=None,
             held_ranks=None, basket_hysteresis=0.0, stop_loss=None):
        from datetime import date, datetime
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from decision import integrated_runner as ir
        from decision.selection import BasketName, SelectionResult
        from core.types import Market

        sel = SelectionResult(
            market="US", as_of=date(2026, 6, 29), regime="neutral",
            target_exposure=exposure, breadth=0.9, avg_conviction=0.05,
            basket=[BasketName(ticker=t, rank_pct=rp, target_weight=1 / len(basket),
                               target_price=None, pred_ret_21d=0.05,
                               band_low=None, band_high=None)
                    for t, rp in basket],
        )
        close_map = {t: 100.0 for t, _ in basket}
        for p in (positions or []):
            close_map.setdefault(p.ticker, 100.0)
        acct = SimpleNamespace(base_currency="USD", balance=1000.0)
        ex = SimpleNamespace(ok=True, fill_price=100.0, fill_volume=1.0, error=None)
        broker = SimpleNamespace(
            get_account=lambda: acct,
            get_positions=lambda: list(positions or []),
            execute=lambda intent: ex,
        )
        risk = SimpleNamespace(check=lambda i, s, l: SimpleNamespace(all_passed=risk_ok))
        # recs df drives rank_map (for basket hysteresis): basket names + any held-name ranks
        rrows = [{"ticker": t, "rank_pct": rp} for t, rp in basket]
        rrows += [{"ticker": tk, "rank_pct": rp} for tk, rp in (held_ranks or {}).items()]
        recs_df = pd.DataFrame(rrows)
        rec = SimpleNamespace(recommend=lambda f, c, cfg=None: recs_df)
        with patch.object(ir, "run_selection", return_value=sel), \
             patch.object(ir, "_tech_score", side_effect=lambda s, m, t, a: tech_scores.get(t)), \
             patch.object(ir, "_risk_state", return_value=None):
            return ir.run_integrated_decisions(
                MagicMock(), market=Market.US, as_of=datetime(2026, 6, 29),
                broker=broker, risk_engine=risk, risk_limits=None,
                recommender=rec, feat_df=__import__("pandas").DataFrame(), close_map=close_map,
                concentrate=concentrate, rebalance_band=rebalance_band,
                direction_score=direction_score, basket_hysteresis=basket_hysteresis,
                stop_loss=stop_loss)

    def _pos(self, ticker, volume=1.0, current_price=100.0):
        from types import SimpleNamespace
        return SimpleNamespace(ticker=ticker, volume=volume, entry_price=100.0,
                               current_price=current_price)

    def test_buys_strong_waits_weak(self):
        rep = self._run(basket=[("A", 0.98), ("B", 0.95), ("C", 0.92)],
                        tech_scores={"A": 50.0, "B": -10.0, "C": 20.0})
        self.assertEqual(rep.buys_executed, 2)   # A, C not bearish
        self.assertEqual(rep.waiting, 1)          # B bearish → delayed (D1, not vetoed)
        self.assertEqual(rep.sells_executed, 0)

    def test_wait_on_missing_tech(self):
        rep = self._run(basket=[("A", 0.98)], tech_scores={})  # no score → wait
        self.assertEqual(rep.buys_executed, 0)
        self.assertEqual(rep.waiting, 1)

    def test_exit_on_basket_drop(self):
        rep = self._run(basket=[("A", 0.98)], tech_scores={"A": 40.0},
                        positions=[self._pos("Z")])   # Z no longer in basket
        self.assertEqual(rep.sells_executed, 1)

    def test_exit_on_tech_breakdown(self):
        rep = self._run(basket=[("A", 0.98)], tech_scores={"A": -40.0},
                        positions=[self._pos("A")])   # held but tech collapsed
        self.assertEqual(rep.sells_executed, 1)

    def test_defensive_closes_all_no_buys(self):
        rep = self._run(basket=[("A", 0.98)], tech_scores={"A": 90.0},
                        positions=[self._pos("A")], exposure=0.0)
        self.assertTrue(rep.defensive)
        self.assertEqual(rep.sells_executed, 1)   # exit_defensive
        self.assertEqual(rep.buys_executed, 0)

    def test_risk_rejection(self):
        rep = self._run(basket=[("A", 0.98)], tech_scores={"A": 50.0}, risk_ok=False)
        self.assertEqual(rep.rejected, 1)
        self.assertEqual(rep.buys_executed, 0)

    def test_merge_default_is_cash(self):
        # concentration is OFF by default (direction-test: switch can't predict
        # forward direction, so we don't bet on it). Cash-style regardless of exposure.
        r = self._run(basket=[("A", 0.98), ("B", 0.95)],
                      tech_scores={"A": 50.0, "B": 50.0}, exposure=0.9)
        self.assertFalse(r.concentrated)
        self.assertEqual(r.buys_executed, 2)

    def test_rebalance_trims_overweight_winner(self):
        # A is held, still in basket, tech OK → normally kept. With a big position
        # (overweight vs equal-weight target) and rebalance_band on, it's trimmed.
        big = self._pos("A", volume=20.0)   # value 2000 vs per-name target ~700
        rep = self._run(basket=[("A", 0.98), ("B", 0.95), ("C", 0.92)],
                        tech_scores={"A": 50.0, "B": 50.0, "C": 50.0},
                        positions=[big], exposure=0.7, rebalance_band=0.3)
        self.assertEqual(rep.trims, 1)
        self.assertEqual(rep.sells_executed, 0)   # trim is not a full exit

    def test_stop_loss_closes_loser(self):
        # A held (still in basket, tech OK) but down 12% → stop-loss (10%) fires first.
        down = self._pos("A", current_price=88.0)   # -12% vs entry 100
        rep = self._run(basket=[("A", 0.98)], tech_scores={"A": 50.0},
                        positions=[down], stop_loss=0.10)
        self.assertEqual(rep.stops, 1)
        self.assertEqual(rep.sells_executed, 1)
        # without stop_loss it would be kept (in basket, tech OK)
        rep2 = self._run(basket=[("A", 0.98)], tech_scores={"A": 50.0}, positions=[down])
        self.assertEqual(rep2.stops, 0)
        self.assertEqual(rep2.sells_executed, 0)

    def test_stop_loss_holds_winner(self):
        up = self._pos("A", current_price=95.0)   # -5%, above the 10% stop → kept
        rep = self._run(basket=[("A", 0.98)], tech_scores={"A": 50.0},
                        positions=[up], stop_loss=0.10)
        self.assertEqual(rep.stops, 0)
        self.assertEqual(rep.sells_executed, 0)

    def test_basket_hysteresis_keeps_held_in_band(self):
        posz = self._pos("Z")   # held, dropped from strict basket (rank 0.86 in cache)
        base = dict(basket=[("A", 0.98), ("B", 0.92)], tech_scores={"A": 50.0, "B": 50.0, "Z": 50.0},
                    positions=[posz], held_ranks={"Z": 0.86})
        r0 = self._run(**base)                              # no hysteresis → dropped → sold
        self.assertEqual(r0.sells_executed, 1)
        r1 = self._run(**base, basket_hysteresis=0.10)      # hold_thr=0.92-0.10=0.82; 0.86≥0.82 → kept
        self.assertEqual(r1.sells_executed, 0)

    def test_no_trim_without_band(self):
        big = self._pos("A", volume=20.0)
        rep = self._run(basket=[("A", 0.98), ("B", 0.95), ("C", 0.92)],
                        tech_scores={"A": 50.0, "B": 50.0, "C": 50.0},
                        positions=[big], exposure=0.7)   # rebalance_band=None → off
        self.assertEqual(rep.trims, 0)

    def test_concentrate_opt_in(self):
        # opt-in + favourable exposure → concentrate; low exposure stays cash even if opted in.
        hi = self._run(basket=[("A", 0.98), ("B", 0.95)],
                       tech_scores={"A": 50.0, "B": 50.0}, exposure=0.7, concentrate=True)
        self.assertTrue(hi.concentrated)
        lo = self._run(basket=[("A", 0.98), ("B", 0.95)],
                       tech_scores={"A": 50.0, "B": 50.0}, exposure=0.4, concentrate=True)
        self.assertFalse(lo.concentrated)         # below threshold → cash even when opted in
        self.assertEqual(lo.buys_executed, 2)

    def test_direction_gated_concentrate(self):
        # with a direction_score, concentrate is gated on it (not the exposure heuristic)
        up = self._run(basket=[("A", 0.98), ("B", 0.95)], tech_scores={"A": 50.0, "B": 50.0},
                       exposure=0.4, concentrate=True, direction_score=0.05)  # up
        self.assertTrue(up.concentrated)          # dir up → concentrate even at low exposure
        dn = self._run(basket=[("A", 0.98), ("B", 0.95)], tech_scores={"A": 50.0, "B": 50.0},
                       exposure=0.9, concentrate=True, direction_score=-0.05)  # down
        self.assertFalse(dn.concentrated)         # dir down → cash even at high exposure
        self.assertEqual(dn.buys_executed, 2)


if __name__ == "__main__":
    unittest.main()
