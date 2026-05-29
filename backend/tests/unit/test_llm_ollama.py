"""Ollama provider tests.

Most tests are pure (no network) and exercise the JSON-schema
validator. The ``OllamaSmokeTest`` actually calls a running Ollama
on localhost and is automatically skipped if the daemon isn't
reachable — so the test suite stays green on machines without
local Ollama set up, while still verifying the real round-trip
on the dev workstation.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# These imports require `httpx`; skip the whole module if not installed.
try:
    import httpx  # noqa: F401

    from core.llm.ollama_provider import OllamaProvider
    _HAVE_HTTPX = True
except ImportError:
    _HAVE_HTTPX = False


@unittest.skipUnless(_HAVE_HTTPX, "httpx not installed (run `uv sync`)")
class OllamaSchemaValidatorTest(unittest.TestCase):
    """Pure unit tests for the internal JSON-Schema subset validator."""

    def test_object_accepts_valid(self):
        schema = {
            "type": "object",
            "required": ["sentiment"],
            "properties": {"sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]}},
        }
        self.assertIsNone(OllamaProvider._validate_against_schema({"sentiment": "positive"}, schema))

    def test_object_missing_required_fails(self):
        schema = {"type": "object", "required": ["sentiment"], "properties": {"sentiment": {"type": "string"}}}
        err = OllamaProvider._validate_against_schema({}, schema)
        self.assertIn("missing required", err or "")

    def test_object_enum_violation(self):
        schema = {"type": "object", "properties": {"x": {"type": "string", "enum": ["a", "b"]}}}
        err = OllamaProvider._validate_against_schema({"x": "c"}, schema)
        self.assertIn("enum", err or "")

    def test_number_bounds(self):
        schema = {"type": "number", "minimum": 0, "maximum": 1}
        self.assertIsNone(OllamaProvider._validate_against_schema(0.5, schema))
        self.assertIn("below minimum", OllamaProvider._validate_against_schema(-0.1, schema) or "")
        self.assertIn("above maximum", OllamaProvider._validate_against_schema(1.5, schema) or "")

    def test_integer_vs_number(self):
        int_schema = {"type": "integer"}
        # bools must NOT pass integer (they're a subtype in Python but
        # semantically wrong for our JSON shape).
        self.assertIn("expected integer", OllamaProvider._validate_against_schema(True, int_schema) or "")
        self.assertIsNone(OllamaProvider._validate_against_schema(42, int_schema))

    def test_array_of_strings(self):
        schema = {"type": "array", "items": {"type": "string"}}
        self.assertIsNone(OllamaProvider._validate_against_schema(["a", "b"], schema))
        self.assertIn("expected string", OllamaProvider._validate_against_schema(["a", 1], schema) or "")

    def test_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "event": {
                    "type": "object",
                    "required": ["kind"],
                    "properties": {"kind": {"type": "string"}},
                },
            },
        }
        self.assertIsNone(OllamaProvider._validate_against_schema({"event": {"kind": "earnings"}}, schema))
        err = OllamaProvider._validate_against_schema({"event": {}}, schema)
        self.assertIn("missing required key: kind", err or "")


def _ollama_reachable() -> bool:
    """Cheap probe that doesn't pollute test discovery output."""
    if not _HAVE_HTTPX:
        return False
    try:
        provider = OllamaProvider()
        return provider.health_check(timeout=3)
    except Exception:
        return False


@unittest.skipUnless(_ollama_reachable(), "local Ollama not reachable on default host")
class OllamaSmokeTest(unittest.TestCase):
    """Real network round-trip against the operator's local Ollama.

    These are NOT marked `slow` because they should run in ~5s on
    qwen2.5:14b; if they get slower than 30s we'd want to know
    (would suggest model loaded into RAM instead of VRAM)."""

    def setUp(self):
        self.provider = OllamaProvider()

    def test_generate_returns_text(self):
        resp = self.provider.generate("Reply with exactly the word OK.", max_tokens=4)
        self.assertEqual(resp.provider, "ollama")
        self.assertGreater(len(resp.text.strip()), 0)
        self.assertIsNotNone(resp.latency_ms)

    def test_classify_returns_valid_json(self):
        schema = {
            "type": "object",
            "required": ["sentiment"],
            "properties": {
                "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
            },
        }
        result = self.provider.classify(
            "Classify the sentiment of this Korean headline: '삼성전자 어닝서프라이즈'. "
            "Reply with JSON like {\"sentiment\": \"positive\"}.",
            schema,
        )
        self.assertIn("sentiment", result)
        self.assertIn(result["sentiment"], ["positive", "neutral", "negative"])


if __name__ == "__main__":
    unittest.main()
