import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from main import app


class StrategyApiTest(unittest.TestCase):
    def test_red_green_status_and_run_once(self):
        with TestClient(app) as client:
            health = client.get("/api/health").json()
            status = client.get("/api/strategy/red-green/status").json()
            run_once = client.post("/api/strategy/red-green/run-once").json()

        self.assertEqual(health["status"], "healthy")
        self.assertTrue(health["strategy_manager"])
        self.assertTrue(status["success"])
        self.assertEqual(status["status"]["id"], "red-green-main")
        self.assertTrue(run_once["success"])
        self.assertTrue(run_once["decision"]["conditions"]["ready"])


if __name__ == "__main__":
    unittest.main()
