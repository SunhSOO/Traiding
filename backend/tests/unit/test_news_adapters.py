"""News adapter tests — every adapter exercised with mocked HTTP/XML."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-please-replace-with-32-random-bytes-x")
os.environ.setdefault("BIGKINDS_ACCESS_KEY", "fake-key")
os.environ.setdefault("NAVER_CLIENT_ID", "fake-id")
os.environ.setdefault("NAVER_CLIENT_SECRET", "fake-secret")

try:
    import httpx  # noqa: F401

    _HAVE_HTTPX = True
except ImportError:
    _HAVE_HTTPX = False


if _HAVE_HTTPX:
    from core.config import reset_settings_cache  # noqa: E402

    reset_settings_cache()

    from data.news.bigkinds import fetch_bigkinds  # noqa: E402
    from data.news.gdelt import fetch_gdelt  # noqa: E402
    from data.news.naver_search import fetch_naver_news  # noqa: E402
    from data.news.rss import fetch_rss  # noqa: E402
    from data.news.types import NewsArticleRow, make_dedup_key  # noqa: E402
    from data.news.wayback import list_snapshots  # noqa: E402


def _fake_resp(payload, status: int = 200, headers=None, content=b"", text=None):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = lambda: None if status < 400 else (_ for _ in ()).throw(RuntimeError("status"))
    resp.json.return_value = payload
    resp.headers = headers or {}
    resp.content = content
    resp.text = text or ""
    return resp


# ──────────────────────────────────────────────────────────────────────
# Types / dedup key
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_HTTPX, "httpx not installed")
class DedupKeyTest(unittest.TestCase):
    def test_stable_across_whitespace_and_case(self):
        a = make_dedup_key(publisher="매경", title="삼성전자 어닝서프라이즈",
                           published_date="2024-04-30")
        b = make_dedup_key(publisher="매경", title="삼성전자  어닝서프라이즈 ",
                           published_date="2024-04-30")
        self.assertEqual(a, b)

    def test_different_publishers_differ(self):
        a = make_dedup_key(publisher="매경", title="X", published_date="2024-04-30")
        b = make_dedup_key(publisher="한경", title="X", published_date="2024-04-30")
        self.assertNotEqual(a, b)


# ──────────────────────────────────────────────────────────────────────
# BIGKinds
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_HTTPX, "httpx not installed")
class BigKindsAdapterTest(unittest.TestCase):
    def test_basic_parse_one_page(self):
        payload = {
            "return_object": {
                "documents": [
                    {"title": "삼성전자 어닝서프라이즈",
                     "provider": "매일경제",
                     "published_at": "2024-04-30T08:00:00",
                     "provider_link_page": "https://www.mk.co.kr/news/123",
                     "tms_raw_stream": "삼성전자가..."},
                    {"title": "SK하이닉스 분기 매출 사상 최대",
                     "provider": "한국경제",
                     "published_at": "2024-04-30",
                     "provider_link_page": "https://www.hankyung.com/article/456"},
                ]
            }
        }
        rows = fetch_bigkinds(
            query="삼성전자",
            start=datetime(2024, 4, 1).date(), end=datetime(2024, 4, 30).date(),
            http_post=lambda u, p: _fake_resp(payload),
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].source, "bigkinds")
        self.assertEqual(rows[0].language, "ko")
        self.assertEqual(rows[0].publisher, "매일경제")
        self.assertEqual(rows[0].published_ts.tzinfo, UTC)

    def test_pagination_stops_when_page_short(self):
        # First page returns 1000, second returns < page_size → stop
        calls = {"n": 0}
        page_size = 1000

        def _post(u, p):
            calls["n"] += 1
            if calls["n"] == 1:
                docs = [{"title": f"T{i}", "provider": "X",
                         "published_at": "2024-01-01",
                         "provider_link_page": f"http://x/{i}"} for i in range(page_size)]
            else:
                docs = [{"title": "last", "provider": "X",
                         "published_at": "2024-01-02",
                         "provider_link_page": "http://x/last"}]
            return _fake_resp({"return_object": {"documents": docs}})

        rows = fetch_bigkinds(
            query="any",
            start=datetime(2024, 1, 1).date(), end=datetime(2024, 1, 5).date(),
            http_post=_post,
            page_size=page_size,
        )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(rows), page_size + 1)

    def test_no_api_key_returns_empty(self):
        os.environ.pop("BIGKINDS_ACCESS_KEY", None)
        reset_settings_cache()
        try:
            rows = fetch_bigkinds(
                query="x",
                start=datetime(2024, 1, 1).date(), end=datetime(2024, 1, 5).date(),
                http_post=lambda u, p: _fake_resp({"return_object": {"documents": []}}),
            )
            self.assertEqual(rows, [])
        finally:
            os.environ["BIGKINDS_ACCESS_KEY"] = "fake-key"
            reset_settings_cache()

    def test_network_failure_short_circuits(self):
        def _boom(u, p):
            raise RuntimeError("DNS")

        rows = fetch_bigkinds(
            query="x", start=datetime(2024, 1, 1).date(), end=datetime(2024, 1, 5).date(),
            http_post=_boom,
        )
        self.assertEqual(rows, [])

    def test_unparseable_timestamp_skipped(self):
        payload = {"return_object": {"documents": [
            {"title": "X", "published_at": "garbage", "provider": "Y"},
        ]}}
        rows = fetch_bigkinds(
            query="x", start=datetime(2024, 1, 1).date(), end=datetime(2024, 1, 5).date(),
            http_post=lambda u, p: _fake_resp(payload),
        )
        self.assertEqual(rows, [])


# ──────────────────────────────────────────────────────────────────────
# GDELT
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_HTTPX, "httpx not installed")
class GdeltAdapterTest(unittest.TestCase):
    def test_basic_parse(self):
        payload = {
            "articles": [
                {"url": "https://reuters.com/article/aapl-earnings",
                 "title": "Apple beats earnings",
                 "seendate": "20240501T140000Z",
                 "domain": "reuters.com",
                 "language": "English"},
                {"url": "https://nikkei.com/article/x",
                 "title": "Topix rallies",
                 "seendate": "20240501T150000Z",
                 "domain": "nikkei.com",
                 "language": "Japanese"},
            ]
        }
        start = datetime(2024, 5, 1, tzinfo=UTC)
        end = datetime(2024, 5, 2, tzinfo=UTC)
        rows = fetch_gdelt(
            query="aapl",
            start=start, end=end,
            http_get=lambda u, p: _fake_resp(payload),
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].source, "gdelt")
        self.assertEqual(rows[0].language, "en")
        self.assertEqual(rows[1].language, "ja")
        self.assertEqual(rows[0].publisher, "reuters.com")

    def test_naive_datetimes_rejected(self):
        with self.assertRaises(ValueError):
            fetch_gdelt(query="x",
                        start=datetime(2024, 5, 1), end=datetime(2024, 5, 2),
                        http_get=lambda u, p: _fake_resp({"articles": []}))

    def test_time_windowing_paginates(self):
        # 5-day window with 24h chunks → 5 GETs
        calls = {"n": 0}
        def _get(u, p):
            calls["n"] += 1
            return _fake_resp({"articles": []})
        start = datetime(2024, 5, 1, tzinfo=UTC)
        end = datetime(2024, 5, 6, tzinfo=UTC)
        fetch_gdelt(query="x", start=start, end=end, http_get=_get, window_hours=24)
        self.assertEqual(calls["n"], 5)


# ──────────────────────────────────────────────────────────────────────
# Naver Search
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_HTTPX, "httpx not installed")
class NaverSearchAdapterTest(unittest.TestCase):
    def test_basic_parse_strips_b_tags(self):
        payload = {
            "items": [
                {"title": "<b>삼성전자</b> 어닝&quot;서프라이즈&quot;",
                 "description": "삼성전자가 <b>큰 폭</b>의 실적을 발표했다.",
                 "originallink": "https://www.mk.co.kr/news/123",
                 "link": "https://news.naver.com/X",
                 "pubDate": "Tue, 30 Apr 2024 14:00:00 +0900"},
            ]
        }
        rows = fetch_naver_news(
            query="삼성전자",
            http_get=lambda u, p, h: _fake_resp(payload),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "naver_search")
        self.assertEqual(rows[0].title, "삼성전자 어닝\"서프라이즈\"")
        self.assertIn("큰 폭", rows[0].summary)
        self.assertNotIn("<b>", rows[0].summary)
        # originallink preferred over Naver-wrapped link
        self.assertEqual(rows[0].url, "https://www.mk.co.kr/news/123")
        self.assertEqual(rows[0].published_ts.tzinfo, UTC)

    def test_no_credentials_returns_empty(self):
        os.environ.pop("NAVER_CLIENT_ID", None)
        reset_settings_cache()
        try:
            rows = fetch_naver_news(query="x",
                                     http_get=lambda u, p, h: _fake_resp({"items": []}))
            self.assertEqual(rows, [])
        finally:
            os.environ["NAVER_CLIENT_ID"] = "fake-id"
            reset_settings_cache()

    def test_pagination_stops_when_items_short(self):
        # First page returns full display, second is short → stop
        calls = {"n": 0}
        display = 100
        def _get(u, p, h):
            calls["n"] += 1
            if calls["n"] == 1:
                items = [{"title": f"T{i}", "description": "",
                          "originallink": f"http://x/{i}",
                          "link": "", "pubDate": "Tue, 30 Apr 2024 14:00:00 +0900"}
                         for i in range(display)]
            else:
                items = [{"title": "last", "description": "",
                          "originallink": "http://x/last",
                          "link": "", "pubDate": "Tue, 30 Apr 2024 14:00:00 +0900"}]
            return _fake_resp({"items": items})

        rows = fetch_naver_news(query="x", http_get=_get, display=display)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(rows), display + 1)

    def test_invalid_pubdate_skipped(self):
        payload = {"items": [{"title": "X", "description": "Y",
                              "originallink": "http://x/1", "link": "",
                              "pubDate": "garbage"}]}
        rows = fetch_naver_news(query="x", http_get=lambda u, p, h: _fake_resp(payload))
        self.assertEqual(rows, [])


# ──────────────────────────────────────────────────────────────────────
# RSS
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_HTTPX, "httpx not installed")
class RssAdapterTest(unittest.TestCase):
    def test_basic_rss20(self):
        xml = ("""<?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0">
              <channel>
                <title>매일경제</title>
                <item>
                  <title>삼성전자 컨센서스 상회</title>
                  <link>https://www.mk.co.kr/news/aaa</link>
                  <description>호실적 발표</description>
                  <pubDate>Tue, 30 Apr 2024 14:00:00 +0900</pubDate>
                </item>
                <item>
                  <title>SK하이닉스 분기 매출 사상 최대</title>
                  <link>https://www.mk.co.kr/news/bbb</link>
                  <description>실적 발표</description>
                  <pubDate>Wed, 01 May 2024 09:00:00 +0900</pubDate>
                </item>
              </channel>
            </rss>""").encode("utf-8")
        rows = fetch_rss(
            "https://www.mk.co.kr/rss/30000001/",
            publisher="매일경제", language="ko",
            http_get=lambda u: _fake_resp({}, content=xml),
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].source.startswith("rss:"))
        self.assertEqual(rows[0].language, "ko")
        self.assertEqual(rows[0].publisher, "매일경제")

    def test_atom_feed(self):
        xml = b"""<?xml version="1.0"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>Apple earnings</title>
                <link href="https://example.com/a"/>
                <published>2024-05-02T14:00:00Z</published>
                <summary>Strong quarter</summary>
              </entry>
            </feed>"""
        rows = fetch_rss(
            "https://example.com/feed.atom",
            publisher="Example", language="en",
            http_get=lambda u: _fake_resp({}, content=xml),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Apple earnings")
        self.assertEqual(rows[0].url, "https://example.com/a")

    def test_bad_xml_returns_empty(self):
        rows = fetch_rss(
            "https://x/", publisher="X", language="ko",
            http_get=lambda u: _fake_resp({}, content=b"<not xml at all"),
        )
        self.assertEqual(rows, [])

    def test_fetch_failure_returns_empty(self):
        def _boom(u):
            raise RuntimeError("net down")
        rows = fetch_rss("https://x/", http_get=_boom)
        self.assertEqual(rows, [])


# ──────────────────────────────────────────────────────────────────────
# Wayback
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_HTTPX, "httpx not installed")
class WaybackAdapterTest(unittest.TestCase):
    def test_list_snapshots_parses_cdx_json(self):
        # CDX returns [header, *rows]
        payload = [
            ["timestamp", "original", "mimetype", "statuscode", "length"],
            ["20210101120000", "https://example.com/x", "text/html", "200", "12345"],
            ["20220601090000", "https://example.com/x", "text/html", "200", "23456"],
        ]
        snaps = list_snapshots(
            "https://example.com/x",
            http_get=lambda u, p: _fake_resp(payload),
        )
        self.assertEqual(len(snaps), 2)
        self.assertEqual(snaps[0]["timestamp"], "20210101120000")
        self.assertEqual(snaps[1]["statuscode"], "200")

    def test_list_empty_when_only_header(self):
        snaps = list_snapshots(
            "https://example.com/x",
            http_get=lambda u, p: _fake_resp([["timestamp"]]),
        )
        self.assertEqual(snaps, [])

    def test_list_fetch_failure(self):
        def _boom(u, p):
            raise RuntimeError("archive.org down")
        snaps = list_snapshots("https://x", http_get=_boom)
        self.assertEqual(snaps, [])


if __name__ == "__main__":
    unittest.main()
