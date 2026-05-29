"""Integration test: red-green strategy via FastAPI.

Gracefully skipped when project dependencies aren't installed yet
(e.g. fresh checkout, before `uv sync`). After Phase 0.3 `uv sync`
this runs end-to-end including the lifespan handler and routing.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from fastapi.testclient import TestClient

    from main import app

    _DEPS_AVAILABLE = True
except ImportError:
    _DEPS_AVAILABLE = False


@unittest.skipUnless(_DEPS_AVAILABLE, "fastapi/project deps not installed (run `uv sync`)")
class StrategyApiTest(unittest.TestCase):
    def test_red_green_status_and_run_once(self):
        with TestClient(app) as client:
            health = client.get("/api/health").json()
            status = client.get("/api/strategy/red-green/status").json()
            run_once = client.post("/api/strategy/red-green/run-once").json()

        self.assertEqual(health["status"], "healthy")
        self.assertTrue(health["red_green_runtime"])
        self.assertTrue(status["success"])
        self.assertEqual(status["status"]["id"], "red-green-main")
        self.assertTrue(run_once["success"])
        self.assertTrue(run_once["decision"]["conditions"]["ready"])


if __name__ == "__main__":
    unittest.main()
