"""Tests for KR (DART) + US (SEC) ticker-identifier enrichment.

External HTTP calls are mocked; everything else is pure parsing logic."""
from __future__ import annotations

import io
import os
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-please-replace-with-32-random-bytes-x")
os.environ.setdefault("DART_API_KEY", "fake-key")
os.environ.setdefault("SEC_USER_AGENT", "test-bot test@example.com")

try:
    import httpx  # noqa: F401
    import sqlalchemy  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from core.config import reset_settings_cache  # noqa: E402

    reset_settings_cache()

    from data.universe.kr_enrichment import (  # noqa: E402
        _parse_corpcode_zip, apply_corp_codes_to_securities,
        fetch_dart_corp_codes,
    )
    from data.universe.us_enrichment import (  # noqa: E402
        apply_ciks_to_securities, fetch_sec_ticker_cik_map,
    )


def _fake_resp(payload, status: int = 200, content: bytes = b""):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = lambda: None if status < 400 else (_ for _ in ()).throw(RuntimeError("status"))
    resp.json.return_value = payload
    resp.content = content
    return resp


def _make_corp_zip(rows: list[tuple[str, str, str]]) -> bytes:
    """Build a corp_code zip given (corp_code, corp_name, stock_code) tuples."""
    items = "\n".join(
        f"<list><corp_code>{c}</corp_code><corp_name>{n}</corp_name>"
        f"<stock_code>{s}</stock_code><modify_date>20240101</modify_date></list>"
        for c, n, s in rows
    )
    xml = f"<?xml version='1.0' encoding='UTF-8'?><result>{items}</result>".encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────
# DART corp_code
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class DartCorpCodeParseTest(unittest.TestCase):
    def test_parses_listed_only(self):
        zip_bytes = _make_corp_zip([
            ("00126380", "삼성전자", "005930"),
            ("00164742", "SK하이닉스", "000660"),
            ("99999999", "비상장회사", ""),     # unlisted — must be dropped
        ])
        m = _parse_corpcode_zip(zip_bytes)
        self.assertEqual(m, {"005930": "00126380", "000660": "00164742"})

    def test_bad_zip_returns_empty(self):
        self.assertEqual(_parse_corpcode_zip(b"not a zip"), {})

    def test_zip_without_xml_returns_empty(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", b"hello")
        self.assertEqual(_parse_corpcode_zip(buf.getvalue()), {})


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class DartCorpCodeFetchTest(unittest.TestCase):
    def test_returns_empty_without_api_key(self):
        os.environ.pop("DART_API_KEY", None)
        reset_settings_cache()
        try:
            out = fetch_dart_corp_codes(
                http_get=lambda u, p: _fake_resp({}, content=b""),
            )
            self.assertEqual(out, {})
        finally:
            os.environ["DART_API_KEY"] = "fake-key"
            reset_settings_cache()

    def test_network_failure_returns_empty(self):
        def _boom(u, p):
            raise RuntimeError("DNS")

        self.assertEqual(fetch_dart_corp_codes(http_get=_boom), {})

    def test_happy_path(self):
        zip_bytes = _make_corp_zip([("00126380", "삼성전자", "005930")])
        out = fetch_dart_corp_codes(
            http_get=lambda u, p: _fake_resp({}, content=zip_bytes),
        )
        self.assertEqual(out, {"005930": "00126380"})


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class ApplyCorpCodesTest(unittest.TestCase):
    def test_writes_only_changed_rows(self):
        from core.models.universe import Security

        a = Security(market="KR", ticker="005930", name="x", currency="KRW",
                     corp_code=None, is_active=True)
        b = Security(market="KR", ticker="000660", name="y", currency="KRW",
                     corp_code="00164742", is_active=True)
        c = Security(market="KR", ticker="066570", name="z", currency="KRW",
                     corp_code=None, is_active=True)

        session = MagicMock()
        session.scalars.return_value = iter([a, b, c])

        out = apply_corp_codes_to_securities(session, {
            "005930": "00126380",  # was None → updated
            "000660": "00164742",  # unchanged → skipped
            "066570": "00401731",  # was None → updated
            "999999": "ZZZ",       # ticker not in DB → ignored
        })
        self.assertEqual(out, 2)
        self.assertEqual(a.corp_code, "00126380")
        self.assertEqual(b.corp_code, "00164742")
        self.assertEqual(c.corp_code, "00401731")

    def test_empty_map_is_noop(self):
        session = MagicMock()
        out = apply_corp_codes_to_securities(session, {})
        self.assertEqual(out, 0)
        session.scalars.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# SEC CIK
# ──────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class SecCikFetchTest(unittest.TestCase):
    def test_basic_shape(self):
        payload = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft"},
            "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway"},
        }
        out = fetch_sec_ticker_cik_map(
            http_get=lambda u, h: _fake_resp(payload),
        )
        self.assertEqual(out["AAPL"], "0000320193")
        self.assertEqual(out["MSFT"], "0000789019")
        # Class-share canonical form
        self.assertEqual(out["BRK.B"], "0001067983")

    def test_missing_user_agent_returns_empty(self):
        os.environ.pop("SEC_USER_AGENT", None)
        reset_settings_cache()
        try:
            out = fetch_sec_ticker_cik_map(
                http_get=lambda u, h: _fake_resp({}),
            )
            self.assertEqual(out, {})
        finally:
            os.environ["SEC_USER_AGENT"] = "test-bot test@example.com"
            reset_settings_cache()

    def test_garbage_entry_skipped(self):
        payload = {
            "0": {"cik_str": 1, "ticker": "X"},
            "1": {"cik_str": "notanumber", "ticker": "Y"},
            "2": {"ticker": "Z"},     # missing cik_str
        }
        out = fetch_sec_ticker_cik_map(http_get=lambda u, h: _fake_resp(payload))
        self.assertEqual(out, {"X": "0000000001"})

    def test_network_failure_returns_empty(self):
        def _boom(u, h):
            raise RuntimeError("DNS")
        self.assertEqual(fetch_sec_ticker_cik_map(http_get=_boom), {})


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class ApplyCiksTest(unittest.TestCase):
    def test_writes_only_changed_rows(self):
        from core.models.universe import Security

        a = Security(market="US", ticker="AAPL", name="Apple", currency="USD",
                     cik=None, is_active=True)
        b = Security(market="US", ticker="MSFT", name="MS", currency="USD",
                     cik="0000789019", is_active=True)

        session = MagicMock()
        session.scalars.return_value = iter([a, b])

        out = apply_ciks_to_securities(session, {
            "AAPL": "0000320193",   # was None → update
            "MSFT": "0000789019",   # unchanged → skip
            "ZZZZ": "ignored",      # not in DB → skip
        })
        self.assertEqual(out, 1)
        self.assertEqual(a.cik, "0000320193")

    def test_empty_map_noop(self):
        session = MagicMock()
        self.assertEqual(apply_ciks_to_securities(session, {}), 0)


if __name__ == "__main__":
    unittest.main()
