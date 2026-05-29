"""Analysis route — mocked DB integration tests.

We mock AsyncSession to verify response shape + filtering without
needing a live PostgreSQL. Real DB integration deferred."""
from __future__ import annotations

import os
import sys
import unittest
import uuid
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

    from core.models.audit import DecisionAudit  # noqa: E402
    from core.models.classifications import ArticleClassification  # noqa: E402
    from core.models.prices import UniverseMembership  # noqa: E402
    from core.models.scores import ModuleScore  # noqa: E402
    from core.models.universe import Security  # noqa: E402
    from routes.analysis import analysis  # noqa: E402


def _make_security():
    return Security(
        market="KR", ticker="005930", name="삼성전자",
        exchange="KOSPI", sector="Semiconductors", industry="IDM",
        currency="KRW", is_active=True,
    )


def _make_score(module, score, conf, hours_ago=0):
    return ModuleScore(
        computed_ts=datetime.now(UTC) - timedelta(hours=hours_ago),
        market="KR", ticker="005930", module=module,
        score=score, confidence=conf,
    )


def _make_decision(action, score):
    return DecisionAudit(
        id=uuid.uuid4(), market="KR", ticker="005930",
        decision_ts=datetime.now(UTC),
        action=action, composite_score=score, composite_confidence=0.7,
    )


@unittest.skipUnless(_HAVE_DEPS, "fastapi/sqlalchemy not installed")
class AnalysisRouteTest(unittest.IsolatedAsyncioTestCase):
    """The route is an async function — we exercise it directly with
    mocked AsyncSession to verify response composition."""

    def _mock_session(
        self, *,
        security=None, scores=None, decisions=None,
        news_rows=None, classifications=None, memberships=None,
    ):
        session = MagicMock()
        session.get = AsyncMock(return_value=security)

        # session.execute returns different things depending on call order.
        # We instead set up scalars() chains that return what we need.
        # In the route:
        #   - _latest_score → execute(...).scalars().first()
        #   - _score_history → execute(...).scalars() (iterable)
        #   - decisions → execute(...).scalars()
        #   - news_rows → execute(...).all()
        #   - classifications → execute(...).scalars()
        #   - memberships → execute(...).scalars()

        score_iter = iter(scores or [])

        def _execute(stmt):
            resp = MagicMock()
            text = str(stmt).lower()
            if "decision_audit" in text:
                resp.scalars = MagicMock(return_value=iter(decisions or []))
            elif "universe_membership" in text:
                resp.scalars = MagicMock(return_value=iter(memberships or []))
            elif "article_classifications" in text:
                resp.scalars = MagicMock(return_value=iter(classifications or []))
            elif "news_ticker_mentions" in text or "news_articles" in text:
                resp.all = MagicMock(return_value=news_rows or [])
            else:
                # module_scores path — alternate latest / history
                row = next(score_iter, None)
                if isinstance(row, list):
                    resp.scalars = MagicMock(return_value=iter(row))
                else:
                    scalars_obj = MagicMock()
                    scalars_obj.first = MagicMock(return_value=row)
                    scalars_obj.__iter__ = lambda self_: iter([row] if row else [])
                    resp.scalars = MagicMock(return_value=scalars_obj)
            return resp

        session.execute = AsyncMock(side_effect=_execute)
        return session

    async def test_unknown_market_400(self):
        session = self._mock_session(security=None)
        with self.assertRaises(Exception) as cm:
            await analysis(market="XX", ticker="005930", user=None, db=session)
        self.assertIn("unknown market", str(cm.exception).lower())

    async def test_unknown_security_404(self):
        session = self._mock_session(security=None)
        with self.assertRaises(Exception) as cm:
            await analysis(market="KR", ticker="999999", user=None, db=session)
        self.assertIn("not found", str(cm.exception).lower())

    async def test_basic_response_shape(self):
        sec = _make_security()
        # Order of session.execute calls in route:
        #   F latest, F history, T latest, T history, I latest, I history,
        #   decisions, news, classifications (only if news_rows), memberships
        scores = [
            _make_score("F", 30, 0.8),                       # F latest
            [_make_score("F", 28, 0.8, hours_ago=24)],       # F history list
            _make_score("T", -10, 0.6),                      # T latest
            [],                                                # T history empty
            None,                                              # I latest absent
            [],                                                # I history empty
        ]
        decisions = [_make_decision("BUY", 45)]
        result = await analysis(
            market="KR", ticker="005930",
            user=None,
            db=self._mock_session(
                security=sec, scores=scores, decisions=decisions,
                news_rows=[], classifications=[], memberships=[],
            ),
        )
        self.assertEqual(result.security.ticker, "005930")
        self.assertEqual(result.security.market, "KR")
        self.assertAlmostEqual(float(result.current_scores["F"].score), 30.0)
        self.assertAlmostEqual(float(result.current_scores["T"].score), -10.0)
        self.assertIsNone(result.current_scores["I"].score)   # missing → None
        self.assertEqual(len(result.recent_decisions), 1)
        self.assertEqual(result.recent_decisions[0].action, "BUY")
        self.assertEqual(result.recent_news, [])
        self.assertEqual(result.memberships, [])


if __name__ == "__main__":
    unittest.main()
