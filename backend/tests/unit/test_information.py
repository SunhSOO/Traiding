"""Information module tests: classifier (mocked LLM) + trust + score.

Type/score/trust tests are pure Python — they always run. The
classifier tests need ``core.llm`` which transitively imports
``httpx``; they skip gracefully when httpx isn't installed."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Always-importable bits
from information.score import score_information  # noqa: E402
from information.trust import (  # noqa: E402
    DEFAULT_SOURCE_TRUST, WeightedMention, source_trust, time_decay_weight,
)
from information.types import (  # noqa: E402
    ArticleClassification, EventType, Horizon, Impact, Sentiment,
)

try:
    import httpx  # noqa: F401

    from information.classifier import (  # noqa: E402
        SCHEMA, _filter_tickers, _summary_grounded, classify_article,
    )

    _HAVE_HTTPX = True
except ImportError:
    _HAVE_HTTPX = False


# ──────────────────────────────────────────────────────────────────────
# Types / enums
# ──────────────────────────────────────────────────────────────────────


class EnumPropertiesTest(unittest.TestCase):
    def test_sentiment_direction(self):
        self.assertEqual(Sentiment.POSITIVE.direction, 1)
        self.assertEqual(Sentiment.NEUTRAL.direction, 0)
        self.assertEqual(Sentiment.NEGATIVE.direction, -1)

    def test_impact_magnitude_order(self):
        self.assertGreater(Impact.HIGH.magnitude, Impact.MEDIUM.magnitude)
        self.assertGreater(Impact.MEDIUM.magnitude, Impact.LOW.magnitude)

    def test_horizon_decay_order(self):
        self.assertLess(Horizon.INTRADAY.decay_days, Horizon.SHORT_TERM.decay_days)
        self.assertLess(Horizon.SHORT_TERM.decay_days, Horizon.MEDIUM_TERM.decay_days)
        self.assertLess(Horizon.MEDIUM_TERM.decay_days, Horizon.LONG_TERM.decay_days)


# ──────────────────────────────────────────────────────────────────────
# Classifier helpers
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_HTTPX, "httpx not installed (run uv sync)")
class TickerFilterTest(unittest.TestCase):
    def test_keeps_valid_kr_us(self):
        out = _filter_tickers(["005930", "AAPL", "BRK.B"], whitelist=None)
        self.assertEqual(set(out), {"005930", "AAPL", "BRK.B"})

    def test_drops_invalid_shapes(self):
        out = _filter_tickers(["abc", "12345", "1234567", "", "@@@", None], whitelist=None)
        self.assertEqual(out, [])

    def test_whitelist_enforced(self):
        out = _filter_tickers(["005930", "AAPL"], whitelist={"005930"})
        self.assertEqual(out, ["005930"])

    def test_uppercases_us_input(self):
        out = _filter_tickers(["aapl"], whitelist=None)
        self.assertEqual(out, ["AAPL"])


@unittest.skipUnless(_HAVE_HTTPX, "httpx not installed (run uv sync)")
class SummaryGroundingTest(unittest.TestCase):
    def test_grounded_when_phrase_overlaps(self):
        # 3-word phrase 'beat earnings consensus' appears in body
        self.assertTrue(_summary_grounded(
            "Apple beat earnings consensus this quarter",
            title="Apple Q1 results",
            body="Apple beat earnings consensus by a wide margin.",
        ))

    def test_ungrounded_pure_hallucination(self):
        self.assertFalse(_summary_grounded(
            "Tesla launches submarine factory in Antarctica.",
            title="Quarterly update",
            body="Apple reports steady iPhone sales for the quarter.",
        ))

    def test_short_summary_passes(self):
        # < 4 words → guard returns True to avoid false negatives
        self.assertTrue(_summary_grounded(
            "Apple beat", title="Apple Q1", body="Some body",
        ))

    def test_short_source_passes(self):
        # < 20 chars source → guard skips
        self.assertTrue(_summary_grounded(
            "Some summary text here that is long",
            title="X", body="Y",
        ))


# ──────────────────────────────────────────────────────────────────────
# Classifier with mocked LLM
# ──────────────────────────────────────────────────────────────────────


class _MockProvider:
    name = "mock"

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def classify(self, prompt: str, schema: dict, max_retries: int = 2):
        self.calls += 1
        return self.payload

    def generate(self, *a, **k):
        raise NotImplementedError

    def health_check(self, *a, **k):
        return True


@unittest.skipUnless(_HAVE_HTTPX, "httpx not installed (run uv sync)")
class ClassifierTest(unittest.TestCase):
    def test_basic_classification(self):
        provider = _MockProvider({
            "event_type": "EARNINGS",
            "sentiment": "POSITIVE",
            "impact": "HIGH",
            "horizon": "SHORT_TERM",
            "confidence": 0.85,
            "summary": "Samsung beat earnings consensus by a wide margin.",
            "tickers_mentioned": ["005930"],
        })
        result = classify_article(
            title="삼성전자 실적 컨센서스 상회",
            summary="Samsung beat earnings consensus by a wide margin this quarter.",
            publisher="MK",
            language="ko", published_iso="2024-04-30T00:00:00+00:00",
            provider=provider,
            ticker_whitelist={"005930"},
        )
        self.assertEqual(result.event_type, EventType.EARNINGS)
        self.assertEqual(result.sentiment, Sentiment.POSITIVE)
        self.assertEqual(result.impact, Impact.HIGH)
        self.assertEqual(result.confidence, 0.85)
        self.assertEqual(result.tickers_mentioned, ("005930",))

    def test_unknown_ticker_dropped_by_whitelist(self):
        provider = _MockProvider({
            "event_type": "OTHER", "sentiment": "NEUTRAL", "impact": "LOW",
            "horizon": "SHORT_TERM", "confidence": 0.5,
            "summary": "Apple comment summary phrase reference.",
            "tickers_mentioned": ["AAPL", "FAKE"],
        })
        result = classify_article(
            title="Apple summary phrase reference", summary="Apple comment summary phrase reference for the test.",
            provider=provider, ticker_whitelist={"AAPL"},
        )
        self.assertEqual(result.tickers_mentioned, ("AAPL",))

    def test_ungrounded_summary_caps_confidence(self):
        provider = _MockProvider({
            "event_type": "PRODUCT", "sentiment": "POSITIVE", "impact": "HIGH",
            "horizon": "MEDIUM_TERM", "confidence": 0.95,
            "summary": "Tesla launches submarine factory in Antarctica today.",
            "tickers_mentioned": [],
        })
        result = classify_article(
            title="Apple Q1 results",
            summary="Apple reports steady iPhone sales for the quarter.",
            provider=provider, ticker_whitelist=set(),
        )
        # Hallucinated summary → confidence capped at 0.5
        self.assertLessEqual(result.confidence, 0.5)

    def test_llm_failure_returns_safe_fallback(self):
        class _BoomProvider:
            name = "boom"
            def classify(self, *a, **k):
                from core.llm import LLMError
                raise LLMError("model unreachable")
            def generate(self, *a, **k): pass
            def health_check(self, *a, **k): return False

        result = classify_article(
            title="x", summary="y",
            provider=_BoomProvider(),
            ticker_whitelist=set(),
        )
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.event_type, EventType.OTHER)
        self.assertEqual(result.sentiment, Sentiment.NEUTRAL)

    def test_bad_enum_value_returns_fallback(self):
        provider = _MockProvider({
            "event_type": "WHATEVER",       # not in enum
            "sentiment": "POSITIVE", "impact": "HIGH",
            "horizon": "SHORT_TERM", "confidence": 0.9,
            "summary": "x", "tickers_mentioned": [],
        })
        result = classify_article(
            title="x", summary="y", provider=provider,
            ticker_whitelist=set(),
        )
        self.assertEqual(result.confidence, 0.0)


# ──────────────────────────────────────────────────────────────────────
# Trust + decay
# ──────────────────────────────────────────────────────────────────────


class TrustTest(unittest.TestCase):
    def test_dart_highest_trust(self):
        self.assertEqual(source_trust("dart"), 1.0)
        self.assertEqual(source_trust("edgar"), 1.0)

    def test_rss_source_falls_back_to_generic(self):
        self.assertEqual(source_trust("rss:somenewspaper.com"), 0.75)

    def test_unknown_source_default(self):
        self.assertEqual(source_trust("mysterious"), 0.5)

    def test_overrides_take_effect(self):
        self.assertEqual(
            source_trust("naver_search", overrides={"naver_search": 0.4}),
            0.4,
        )


class DecayTest(unittest.TestCase):
    def test_fresh_news_full_weight(self):
        self.assertAlmostEqual(time_decay_weight(0, Horizon.SHORT_TERM), 1.0)

    def test_half_life_at_horizon_decay_days(self):
        w = time_decay_weight(Horizon.SHORT_TERM.decay_days, Horizon.SHORT_TERM)
        self.assertAlmostEqual(w, 0.5)

    def test_longer_horizon_decays_slower(self):
        wa = time_decay_weight(30, Horizon.INTRADAY)
        wb = time_decay_weight(30, Horizon.LONG_TERM)
        self.assertLess(wa, wb)

    def test_negative_age_clamped(self):
        self.assertAlmostEqual(time_decay_weight(-5, Horizon.SHORT_TERM), 1.0)


# ──────────────────────────────────────────────────────────────────────
# Score aggregation
# ──────────────────────────────────────────────────────────────────────


def _make_mention(direction=1, impact=1.0, confidence=1.0, trust=1.0, decay=1.0):
    return WeightedMention(
        ticker="005930", market="KR",
        direction=direction, impact_magnitude=impact,
        confidence=confidence, source_trust=trust, time_weight=decay,
    )


class ScoreInformationTest(unittest.TestCase):
    def test_no_mentions_zero(self):
        s = score_information([])
        self.assertEqual(s.score, 0.0)
        self.assertEqual(s.confidence, 0.0)
        self.assertEqual(s.n_mentions, 0)

    def test_single_strong_positive(self):
        s = score_information([_make_mention(direction=1)])
        self.assertGreater(s.score, 0)
        self.assertGreater(s.confidence, 0)
        self.assertEqual(s.n_mentions, 1)

    def test_strong_negative(self):
        s = score_information([_make_mention(direction=-1)])
        self.assertLess(s.score, 0)

    def test_clipped_to_range(self):
        many = [_make_mention(direction=1, impact=1.0, confidence=1.0) for _ in range(100)]
        s = score_information(many)
        self.assertLessEqual(s.score, 100.0)
        self.assertGreaterEqual(s.score, -100.0)

    def test_confidence_saturates(self):
        few = [_make_mention() for _ in range(50)]
        s = score_information(few)
        self.assertLess(s.confidence, 1.0 + 1e-6)
        self.assertGreater(s.confidence, 0.99)

    def test_low_quality_mentions_yield_low_confidence(self):
        weak = [_make_mention(confidence=0.1, trust=0.5, decay=0.5, impact=0.2)
                for _ in range(3)]
        s = score_information(weak)
        self.assertLess(s.confidence, 0.5)

    def test_neutral_mentions_dont_move_score(self):
        s = score_information([
            _make_mention(direction=0),
            _make_mention(direction=0),
        ])
        self.assertEqual(s.score, 0.0)
        # But they still count toward confidence
        self.assertGreater(s.confidence, 0)


if __name__ == "__main__":
    unittest.main()
