"""Ticker-mapper and news loader tests."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import sqlalchemy  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from core.types import Market  # noqa: E402
    from data.news.loader import NewsSourceSpec, sync_news  # noqa: E402
    from data.news.ticker_mapper import NameIndex, map_article  # noqa: E402
    from data.news.types import NewsArticleRow  # noqa: E402


def _row(title="x", summary="", published_ts=None, url=None, publisher="P", source="src"):
    return NewsArticleRow(
        source=source, title=title, summary=summary,
        published_ts=published_ts or datetime(2024, 5, 1, 12, tzinfo=UTC),
        url=url, publisher=publisher, language="ko",
        as_of_ts=published_ts or datetime(2024, 5, 1, 12, tzinfo=UTC),
    )


# ──────────────────────────────────────────────────────────────────────
# Ticker mapper
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class TickerMapperTest(unittest.TestCase):
    def setUp(self):
        self.index = NameIndex.from_pairs(
            kr=[("삼성전자", "005930"), ("SK하이닉스", "000660"), ("LG전자", "066570")],
            us=[("Apple Inc.", "AAPL"), ("Microsoft Corporation", "MSFT"),
                ("Berkshire Hathaway Inc.", "BRK.B"), ("Tesla, Inc.", "TSLA")],
        )

    def test_kr_title_match(self):
        mentions = map_article(
            title="삼성전자 어닝 서프라이즈", summary="실적 호조",
            source_tags=(), index=self.index,
        )
        kr = [m for m in mentions if m.market == Market.KR]
        self.assertEqual(len(kr), 1)
        self.assertEqual(kr[0].ticker, "005930")
        self.assertEqual(kr[0].mention_kind, "title")

    def test_us_title_match_with_suffix_strip(self):
        mentions = map_article(
            title="Apple beats earnings, Tesla disappoints",
            summary="", source_tags=(), index=self.index,
        )
        us = sorted([m for m in mentions if m.market == Market.US], key=lambda m: m.ticker)
        tickers = {m.ticker for m in us}
        self.assertIn("AAPL", tickers)
        self.assertIn("TSLA", tickers)

    def test_uppercase_ticker_with_dollar_prefix(self):
        mentions = map_article(
            title="$AAPL hits new all-time high",
            summary="", source_tags=(), index=self.index,
        )
        self.assertTrue(any(m.ticker == "AAPL" for m in mentions))

    def test_title_plus_body_bonus(self):
        mentions = map_article(
            title="삼성전자 컨센서스 상회",
            summary="삼성전자가 분기 영업이익 컨센서스를 크게 상회했다.",
            source_tags=(), index=self.index,
        )
        m = next(m for m in mentions if m.ticker == "005930")
        self.assertEqual(m.mention_kind, "title")
        self.assertGreater(m.relevance, 0.9)

    def test_source_tag_is_authoritative(self):
        mentions = map_article(
            title="generic market commentary",
            summary="",
            source_tags=("005930",), index=self.index,
        )
        m = next(m for m in mentions if m.ticker == "005930")
        self.assertEqual(m.mention_kind, "tag")
        self.assertEqual(m.relevance, 1.0)

    def test_short_english_word_does_not_match_short_name(self):
        # 'it' should NOT match a hypothetical 2-letter ticker / name
        index = NameIndex.from_pairs(
            kr=[],
            us=[("AT", "T"), ("It", "IT")],   # 'AT' length 2, 'It' length 2
        )
        mentions = map_article(
            title="It works at last", summary="",
            source_tags=(), index=index,
        )
        # Both short single-word names rejected by the precision guard
        self.assertEqual([m for m in mentions if m.market == Market.US], [])

    def test_top_n_caps_results(self):
        index = NameIndex.from_pairs(
            kr=[("삼성전자", "005930"), ("SK하이닉스", "000660"),
                ("LG전자", "066570"), ("현대차", "005380"),
                ("기아", "000270"), ("네이버", "035420"),
                ("카카오", "035720")],
            us=[],
        )
        title = "삼성전자 SK하이닉스 LG전자 현대차 기아 네이버 카카오 모두 상승"
        mentions = map_article(title=title, summary="", source_tags=(), index=index, top_n=3)
        self.assertEqual(len(mentions), 3)


# ──────────────────────────────────────────────────────────────────────
# Loader orchestration
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy not installed")
class NewsLoaderTest(unittest.TestCase):
    def _session(self) -> MagicMock:
        session = MagicMock()
        # _build_name_index iterates scalars(...) and reads attrs;
        # by default give it empty KR + US so mapping yields no mentions.
        session.scalars.return_value.__iter__ = lambda self_: iter([])
        # session.scalars(...).first() returns None for freshness lookups
        session.scalars.return_value.first.return_value = None
        # rowcount for INSERT ON CONFLICT
        session.execute.return_value.rowcount = 1
        return session

    def test_runs_each_enabled_source(self):
        session = self._session()
        calls = {"a": 0, "b": 0, "c": 0}

        def _src(name, count=2):
            def _fetch():
                calls[name] += 1
                return [_row(title=f"{name}-{i}", url=f"http://{name}/{i}") for i in range(count)]
            return _fetch

        report = sync_news(
            session,
            sources=[
                NewsSourceSpec(name="a", fetcher=_src("a")),
                NewsSourceSpec(name="b", fetcher=_src("b")),
                NewsSourceSpec(name="c", fetcher=_src("c"), enabled=False),
            ],
        )
        self.assertEqual(calls["a"], 1)
        self.assertEqual(calls["b"], 1)
        self.assertEqual(calls["c"], 0)  # disabled
        self.assertEqual(report.sources_processed, 2)
        self.assertEqual(report.sources_failed, 0)
        self.assertEqual(report.articles_seen, 4)

    def test_in_batch_dedup_by_url(self):
        session = self._session()
        rows = [
            _row(title="x", url="http://x", published_ts=datetime(2024, 5, 1, tzinfo=UTC)),
            _row(title="x updated", url="http://x", published_ts=datetime(2024, 5, 2, tzinfo=UTC)),
            _row(title="y", url="http://y", published_ts=datetime(2024, 5, 1, tzinfo=UTC)),
        ]
        report = sync_news(
            session,
            sources=[NewsSourceSpec(name="src", fetcher=lambda: rows)],
        )
        # 3 seen, 1 deduped → 2 unique
        self.assertEqual(report.articles_seen, 3)
        self.assertEqual(report.articles_skipped_dedup, 1)

    def test_in_batch_dedup_by_content_hash_when_no_url(self):
        session = self._session()
        rows = [
            _row(title="x", url=None, publisher="P",
                 published_ts=datetime(2024, 5, 1, tzinfo=UTC)),
            _row(title="x", url=None, publisher="P",
                 published_ts=datetime(2024, 5, 1, 12, tzinfo=UTC)),
        ]
        report = sync_news(
            session,
            sources=[NewsSourceSpec(name="src", fetcher=lambda: rows)],
        )
        self.assertEqual(report.articles_skipped_dedup, 1)

    def test_source_failure_isolated(self):
        session = self._session()
        good = NewsSourceSpec(name="good", fetcher=lambda: [_row(url="http://g/1")])

        def _boom():
            raise RuntimeError("api down")
        bad = NewsSourceSpec(name="bad", fetcher=_boom)

        report = sync_news(session, sources=[good, bad])
        self.assertEqual(report.sources_processed, 1)
        self.assertEqual(report.sources_failed, 1)
        self.assertEqual(report.articles_seen, 1)
        self.assertEqual(len(report.errors), 1)

    def test_empty_sources(self):
        session = self._session()
        report = sync_news(session, sources=[])
        self.assertEqual(report.sources_processed, 0)
        self.assertEqual(report.articles_seen, 0)


if __name__ == "__main__":
    unittest.main()
