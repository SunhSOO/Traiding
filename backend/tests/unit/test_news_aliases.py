"""Alias-aware NameIndex tests.

Pure module — alias dictionary + ticker mapper. No DB, no LLM."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import structlog  # noqa: F401  — keeps the deps-skip pattern consistent

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False

if _HAVE_DEPS:
    from core.types import Market  # noqa: E402
    from data.news.aliases import AliasEntry, seed_aliases  # noqa: E402
    from data.news.ticker_mapper import NameIndex, map_article  # noqa: E402


@unittest.skipUnless(_HAVE_DEPS, "deps")
class SeedAliasesTest(unittest.TestCase):
    def test_contains_known_kr_aliases(self):
        aliases = seed_aliases()
        kr = {(a.alias, a.ticker) for a in aliases if a.market == "KR"}
        # A few hand-checked anchors that must survive any future curation
        self.assertIn(("Samsung Electronics", "005930"), kr)
        self.assertIn(("삼전", "005930"), kr)
        self.assertIn(("SK Hynix", "000660"), kr)
        self.assertIn(("Kakao", "035720"), kr)

    def test_contains_known_us_aliases(self):
        aliases = seed_aliases()
        us = {(a.alias, a.ticker) for a in aliases if a.market == "US"}
        self.assertIn(("애플", "AAPL"), us)
        self.assertIn(("엔비디아", "NVDA"), us)
        self.assertIn(("테슬라", "TSLA"), us)


@unittest.skipUnless(_HAVE_DEPS, "deps")
class NameIndexWithAliasesTest(unittest.TestCase):
    def _index(self):
        return NameIndex.from_pairs(
            kr=[("삼성전자", "005930"), ("SK하이닉스", "000660")],
            us=[("Apple Inc.", "AAPL"), ("NVIDIA Corporation", "NVDA")],
        )

    def test_alias_matches_english_in_kr_news(self):
        idx = self._index()
        # Korean article using the English brand name
        mentions = map_article(
            title="Samsung Electronics announces new chip",
            summary="",
            source_tags=[],
            index=idx,
        )
        tickers = {(m.market, m.ticker) for m in mentions}
        self.assertIn((Market.KR, "005930"), tickers)

    def test_alias_matches_korean_short_form(self):
        idx = self._index()
        mentions = map_article(
            title="삼전 4분기 실적 발표",
            summary="",
            source_tags=[],
            index=idx,
        )
        tickers = {(m.market, m.ticker) for m in mentions}
        self.assertIn((Market.KR, "005930"), tickers)

    def test_alias_matches_korean_us_brand(self):
        idx = self._index()
        mentions = map_article(
            title="애플 신제품 출시",
            summary="",
            source_tags=[],
            index=idx,
        )
        tickers = {(m.market, m.ticker) for m in mentions}
        self.assertIn((Market.US, "AAPL"), tickers)

    def test_alias_for_ticker_not_in_universe_is_dropped(self):
        # PLTR is in the alias seed but NOT in this universe
        idx = NameIndex.from_pairs(
            kr=[],
            us=[("Apple Inc.", "AAPL")],
        )
        mentions = map_article(
            title="팔란티어 신규 계약",
            summary="",
            source_tags=[],
            index=idx,
        )
        self.assertEqual(mentions, [])

    def test_canonical_name_wins_over_alias(self):
        # Canonical name + alias point to same ticker; canonical not
        # overwritten when alias re-registers.
        idx = NameIndex.from_pairs(
            kr=[("삼성전자", "005930")],
            us=[],
        )
        # 삼성전자 must still resolve to 005930 (canonical preserved)
        self.assertEqual(idx.kr_name_to_ticker.get("삼성전자"), "005930")


@unittest.skipUnless(_HAVE_DEPS, "deps")
class LLMTickerIntegrationTest(unittest.TestCase):
    def _index(self):
        return NameIndex.from_pairs(
            kr=[("삼성전자", "005930")],
            us=[("Apple Inc.", "AAPL"), ("NVIDIA Corporation", "NVDA")],
            include_seed_aliases=False,
        )

    def test_llm_ticker_added_when_in_universe(self):
        idx = self._index()
        mentions = map_article(
            title="vague mention", summary="", source_tags=[],
            index=idx,
            llm_tickers=["NVDA"],
        )
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0].ticker, "NVDA")
        self.assertEqual(mentions[0].mention_kind, "llm")
        self.assertAlmostEqual(mentions[0].relevance, 0.7)

    def test_llm_ticker_dropped_when_not_in_universe(self):
        idx = self._index()
        mentions = map_article(
            title="vague", summary="", source_tags=[], index=idx,
            llm_tickers=["XXXFAKE"],
        )
        self.assertEqual(mentions, [])

    def test_llm_corroborates_title_hit_boosts_relevance(self):
        idx = self._index()
        mentions = map_article(
            title="Apple announces earnings",
            summary="",
            source_tags=[],
            index=idx,
            llm_tickers=["AAPL"],
        )
        aapl = next(m for m in mentions if m.ticker == "AAPL")
        # Title hit base = 0.9; LLM corroboration = +0.05 → 0.95
        self.assertAlmostEqual(aapl.relevance, 0.95, places=4)
        # Kind stays "title" so the audit trail shows the strongest source
        self.assertEqual(aapl.mention_kind, "title")

    def test_llm_handles_kr_6digit_form(self):
        idx = self._index()
        mentions = map_article(
            title="vague", summary="", source_tags=[], index=idx,
            llm_tickers=["005930"],
        )
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0].market, Market.KR)
        self.assertEqual(mentions[0].ticker, "005930")


if __name__ == "__main__":
    unittest.main()
