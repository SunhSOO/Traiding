"""Pure scan-engine tests — no DB / FastAPI."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from decision.types import Action, DecisionConfig  # noqa: E402
from scan.engine import (  # noqa: E402
    ScanRequest, TickerScoreSnapshot, scan_signals,
)


def _snap(ts, *, f=None, fc=0.9, t=None, tc=0.9, i=None, ic=0.9,
          cluster=None, market="KR", ticker="005930"):
    return TickerScoreSnapshot(
        market=market, ticker=ticker, name="Test",
        fundamental_ts=ts if f is not None else None,
        fundamental_score=f, fundamental_confidence=fc if f is not None else None,
        technical_ts=ts if t is not None else None,
        technical_score=t, technical_confidence=tc if t is not None else None,
        information_ts=ts if i is not None else None,
        information_score=i, information_confidence=ic if i is not None else None,
        cluster_id=cluster,
    )


def _request(as_of=None, **kwargs):
    return ScanRequest(
        decision_config=DecisionConfig(),
        as_of=as_of or datetime.now(UTC),
        **kwargs,
    )


# ──────────────────────────────────────────────────────────────────────


class ActionMappingTest(unittest.TestCase):
    def test_strong_positive_triggers_buy(self):
        now = datetime.now(UTC)
        out = scan_signals(
            [_snap(now, f=80, t=70, i=60)],
            request=_request(as_of=now, min_overall_confidence=0.0),
        )
        self.assertEqual(out[0].action, Action.BUY)
        self.assertGreater(out[0].composite_score, 25.0)

    def test_strong_negative_triggers_sell(self):
        now = datetime.now(UTC)
        out = scan_signals(
            [_snap(now, f=-70, t=-60, i=-50)],
            request=_request(as_of=now, min_overall_confidence=0.0),
        )
        self.assertEqual(out[0].action, Action.SELL)

    def test_neutral_score_holds(self):
        now = datetime.now(UTC)
        out = scan_signals(
            [_snap(now, f=10, t=5, i=0)],
            request=_request(as_of=now, min_overall_confidence=0.0),
        )
        self.assertEqual(out[0].action, Action.HOLD)
        self.assertIn("임계값", out[0].reason)

    def test_no_modules_returns_hold_with_reason(self):
        now = datetime.now(UTC)
        # All ts None → no contributing modules
        out = scan_signals(
            [TickerScoreSnapshot(market="KR", ticker="999999")],
            request=_request(as_of=now, min_overall_confidence=0.0),
        )
        self.assertEqual(out[0].action, Action.HOLD)
        self.assertEqual(out[0].contributing_modules, ())
        self.assertIn("기여", out[0].reason)

    def test_low_confidence_holds_despite_strong_score(self):
        now = datetime.now(UTC)
        out = scan_signals(
            [_snap(now, f=90, fc=0.10, t=80, tc=0.10, i=70, ic=0.10)],
            request=_request(as_of=now, min_overall_confidence=0.40),
        )
        self.assertEqual(out[0].action, Action.HOLD)
        self.assertIn("confidence", out[0].reason.lower())


class StalenessTest(unittest.TestCase):
    def test_stale_score_dropped_from_composite(self):
        now = datetime.now(UTC)
        # F is fresh, T and I are stale (older than default 48h)
        recent = now - timedelta(hours=1)
        ancient = now - timedelta(hours=200)
        snap = TickerScoreSnapshot(
            market="KR", ticker="005930",
            fundamental_ts=recent, fundamental_score=90, fundamental_confidence=0.9,
            technical_ts=ancient, technical_score=80, technical_confidence=0.9,
            information_ts=ancient, information_score=70, information_confidence=0.9,
        )
        # Use default min_overall_confidence=0.40 to test that low
        # coverage (1/3 modules) drops below the floor.
        out = scan_signals([snap], request=_request(as_of=now))
        self.assertEqual(out[0].contributing_modules, ("F",))
        # Composite is still strong because of F=90 alone
        self.assertGreater(out[0].composite_score, 25.0)
        # But low coverage drops confidence below 0.4 → HOLD
        self.assertEqual(out[0].action, Action.HOLD)
        self.assertIn("confidence", out[0].reason.lower())

    def test_stalest_module_reported(self):
        now = datetime.now(UTC)
        snap = TickerScoreSnapshot(
            market="KR", ticker="005930",
            fundamental_ts=now - timedelta(hours=1), fundamental_score=10, fundamental_confidence=0.9,
            technical_ts=now - timedelta(hours=30), technical_score=10, technical_confidence=0.9,
            information_ts=now - timedelta(hours=5), information_score=10, information_confidence=0.9,
        )
        out = scan_signals([snap], request=_request(as_of=now, min_overall_confidence=0.0))
        self.assertEqual(out[0].stalest_module, "T")
        self.assertAlmostEqual(out[0].stalest_age_hours, 30.0, places=1)


class ClusterWeightsTest(unittest.TestCase):
    def test_cluster_override_changes_composite(self):
        now = datetime.now(UTC)
        # F=80, T=-50, I=0; default weights ≈ HOLD
        snap = _snap(now, f=80, t=-50, i=0, cluster="CL1")
        cfg = DecisionConfig(cluster_weight_overrides={
            "CL1": {"F": 1.0, "T": 0.0, "I": 0.0},
        })
        request = ScanRequest(
            decision_config=cfg, as_of=now, min_overall_confidence=0.0,
        )
        out = scan_signals([snap], request=request)
        self.assertEqual(out[0].action, Action.BUY)
        # Weights used should reflect the override (F=1.0)
        self.assertAlmostEqual(out[0].weights_used["F"], 1.0)

    def test_no_cluster_uses_default_weights(self):
        now = datetime.now(UTC)
        snap = _snap(now, f=80, t=-50, i=0)  # no cluster_id
        cfg = DecisionConfig(cluster_weight_overrides={
            "CL1": {"F": 1.0, "T": 0.0, "I": 0.0},
        })
        request = ScanRequest(
            decision_config=cfg, as_of=now, min_overall_confidence=0.0,
        )
        out = scan_signals([snap], request=request)
        # Default normalized weights apply → composite isn't pure F → HOLD
        self.assertEqual(out[0].action, Action.HOLD)


if __name__ == "__main__":
    unittest.main()
