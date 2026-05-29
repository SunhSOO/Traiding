"""Re-scoring engine tests — pure functions, no DB.

Covers:
- All three modules required (gate by freshness)
- Confidence gate
- Threshold action mapping
- Cooldown between same-ticker decisions
- Cluster weights override flow
- Stale F/T/I dropped before composite
"""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.rescoring import (  # noqa: E402
    ModuleScorePoint, RescoringConfig, rescore_to_decisions,
    to_replay_signals,
)
from decision.types import Action, DecisionConfig  # noqa: E402


def _pt(d: datetime, market: str, ticker: str, module: str,
        score: float, confidence: float = 0.9) -> ModuleScorePoint:
    return ModuleScorePoint(
        ts=d, market=market, ticker=ticker, module=module,
        score=score, confidence=confidence,
    )


def _triple(ts: datetime, *, market="KR", ticker="005930",
            f=80.0, t=80.0, i=80.0, conf=0.9):
    """Helper: emit F+T+I at the same ts (typical synchronous scoring run)."""
    return [
        _pt(ts, market, ticker, "F", f, conf),
        _pt(ts, market, ticker, "T", t, conf),
        _pt(ts, market, ticker, "I", i, conf),
    ]


# ──────────────────────────────────────────────────────────────────────


class RescoreBasicTest(unittest.TestCase):
    def test_no_points_no_decisions(self):
        out = rescore_to_decisions([], decision_config=DecisionConfig())
        self.assertEqual(out, [])

    def test_strong_buy_signal_emits_buy(self):
        ts = datetime(2026, 5, 20, 16, tzinfo=UTC)
        out = rescore_to_decisions(
            _triple(ts, f=90, t=80, i=70, conf=0.9),
            decision_config=DecisionConfig(),
            rescoring_config=RescoringConfig(min_overall_confidence=0.0),
        )
        # The first two points (F + T) can't produce a decision (need I).
        # The third (I) closes the triple → 1 BUY decision.
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].action, Action.BUY)
        self.assertGreater(out[0].composite_score, 25.0)

    def test_strong_sell_signal(self):
        ts = datetime(2026, 5, 20, 16, tzinfo=UTC)
        out = rescore_to_decisions(
            _triple(ts, f=-80, t=-70, i=-60, conf=0.9),
            decision_config=DecisionConfig(),
            rescoring_config=RescoringConfig(min_overall_confidence=0.0),
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].action, Action.SELL)

    def test_neutral_score_no_decision(self):
        ts = datetime(2026, 5, 20, 16, tzinfo=UTC)
        out = rescore_to_decisions(
            _triple(ts, f=5, t=5, i=5, conf=0.9),
            decision_config=DecisionConfig(),
            rescoring_config=RescoringConfig(min_overall_confidence=0.0),
        )
        self.assertEqual(out, [])

    def test_missing_module_no_decision(self):
        ts = datetime(2026, 5, 20, 16, tzinfo=UTC)
        # Only F + T, no I.
        out = rescore_to_decisions(
            [_pt(ts, "KR", "005930", "F", 80),
             _pt(ts, "KR", "005930", "T", 80)],
            decision_config=DecisionConfig(),
            rescoring_config=RescoringConfig(min_overall_confidence=0.0),
        )
        self.assertEqual(out, [])

    def test_low_confidence_blocks(self):
        ts = datetime(2026, 5, 20, 16, tzinfo=UTC)
        out = rescore_to_decisions(
            _triple(ts, f=80, t=80, i=80, conf=0.20),
            decision_config=DecisionConfig(),
            # Default cfg min_overall_confidence=0.40
            rescoring_config=RescoringConfig(),
        )
        self.assertEqual(out, [])


class CooldownTest(unittest.TestCase):
    def test_back_to_back_signals_collapsed(self):
        d1 = datetime(2026, 5, 20, 16, tzinfo=UTC)
        d2 = d1 + timedelta(hours=2)
        out = rescore_to_decisions(
            _triple(d1) + _triple(d2),
            decision_config=DecisionConfig(),
            rescoring_config=RescoringConfig(
                min_overall_confidence=0.0, decision_cooldown_days=1,
            ),
        )
        # Two triples on same day → only one BUY due to cooldown
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].ts, d1)

    def test_decisions_emitted_after_cooldown(self):
        d1 = datetime(2026, 5, 20, 16, tzinfo=UTC)
        d2 = d1 + timedelta(days=2)
        out = rescore_to_decisions(
            _triple(d1) + _triple(d2),
            decision_config=DecisionConfig(),
            rescoring_config=RescoringConfig(
                min_overall_confidence=0.0, decision_cooldown_days=1,
            ),
        )
        self.assertEqual(len(out), 2)


class StalenessTest(unittest.TestCase):
    def test_old_module_score_drops_decision(self):
        # F is from 100 hours ago; T and I are fresh today.
        # Default DecisionConfig.score_staleness_hours = 48
        recent = datetime(2026, 5, 20, 16, tzinfo=UTC)
        ancient = recent - timedelta(hours=100)
        score_points = [
            _pt(ancient, "KR", "005930", "F", 90),
            _pt(recent, "KR", "005930", "T", 80),
            _pt(recent, "KR", "005930", "I", 70),
        ]
        out = rescore_to_decisions(
            score_points,
            decision_config=DecisionConfig(),
            rescoring_config=RescoringConfig(min_overall_confidence=0.0),
        )
        # F is stale → triple is incomplete → no decision
        self.assertEqual(out, [])


class WeightsTest(unittest.TestCase):
    def test_cluster_weights_route_uses_override(self):
        ts = datetime(2026, 5, 20, 16, tzinfo=UTC)
        # F=80, T=-50, I=0
        # With default weights F=0.35, T=0.40, I=0.25 →
        #   composite = (80*0.35 + -50*0.40 + 0*0.25) = 28 - 20 = 8 → HOLD
        # With cluster {F:1.0, T:0, I:0} →
        #   composite = 80 → BUY
        decision_config = DecisionConfig(
            cluster_weight_overrides={
                "CL1": {"F": 1.0, "T": 0.0, "I": 0.0},
            },
        )
        rescoring_config = RescoringConfig(
            min_overall_confidence=0.0,
            ticker_cluster_map={("KR", "005930"): "CL1"},
        )
        out = rescore_to_decisions(
            _triple(ts, f=80, t=-50, i=0),
            decision_config=decision_config,
            rescoring_config=rescoring_config,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].action, Action.BUY)
        self.assertEqual(out[0].cluster_id, "CL1")

    def test_no_cluster_uses_global_default(self):
        ts = datetime(2026, 5, 20, 16, tzinfo=UTC)
        out = rescore_to_decisions(
            _triple(ts, f=80, t=-50, i=0),
            decision_config=DecisionConfig(
                cluster_weight_overrides={"CL1": {"F": 1.0, "T": 0.0, "I": 0.0}}
            ),
            rescoring_config=RescoringConfig(
                min_overall_confidence=0.0,
                ticker_cluster_map={},  # no cluster for this ticker
            ),
        )
        # Default weighting → composite ≈ 8 → HOLD
        self.assertEqual(out, [])


class AdapterTest(unittest.TestCase):
    def test_to_replay_signals_preserves_order(self):
        ts1 = datetime(2026, 5, 20, 16, tzinfo=UTC)
        ts2 = ts1 + timedelta(days=2)
        decisions = rescore_to_decisions(
            _triple(ts1, f=80, t=70, i=70)
            + _triple(ts2, f=-80, t=-70, i=-70),
            decision_config=DecisionConfig(),
            rescoring_config=RescoringConfig(
                min_overall_confidence=0.0, decision_cooldown_days=1,
            ),
        )
        signals = to_replay_signals(decisions)
        self.assertEqual([s.action for s in signals], ["BUY", "SELL"])
        self.assertEqual(signals[0].market, "KR")
        self.assertEqual(signals[0].ticker, "005930")


if __name__ == "__main__":
    unittest.main()
