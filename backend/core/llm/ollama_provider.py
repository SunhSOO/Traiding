"""Ollama HTTP client for the LLM abstraction.

We talk directly to Ollama's HTTP API (``/api/generate``,
``/api/chat``, ``/api/tags``) over httpx rather than depending on
the ``ollama`` Python package — it's a thin wrapper that ties us to
their release cadence and pulls in extra deps we don't need.

The Ollama HTTP API supports a ``format`` parameter that constrains
output to JSON (``format="json"``), which we use for ``classify``.
For stricter JSON schema, Ollama also supports the modern
``format=<json_schema>`` form (added in 2024-12) — we use that
when ``schema`` is provided.

Health-check pings ``/api/tags`` (cheap; returns installed models).
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from core.config import get_settings
from core.llm.base import LLMError, LLMProvider, LLMResponse
from core.logging import get_logger

log = get_logger(__name__)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        host: str | None = None,
        default_model: str | None = None,
    ):
        settings = get_settings()
        self.host = (host or settings.ollama_host).rstrip("/")
        self.default_model = default_model or settings.ollama_default_model

    # ── Health ──
    def health_check(self, *, timeout: float = 5.0) -> bool:
        try:
            r = httpx.get(f"{self.host}/api/tags", timeout=timeout)
            r.raise_for_status()
            return True
        except Exception:
            return False

    def installed_models(self, *, timeout: float = 5.0) -> list[str]:
        try:
            r = httpx.get(f"{self.host}/api/tags", timeout=timeout)
            r.raise_for_status()
            data = r.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            raise LLMError(f"Could not list Ollama models: {e}") from e

    # ── Generation ──
    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout: float = 60.0,
    ) -> LLMResponse:
        return self._call(
            prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            format_param=None,
        )

    # ── Classification (JSON-constrained) ──
    def classify(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        model: str | None = None,
        max_retries: int = 2,
        timeout: float = 90.0,
    ) -> dict[str, Any]:
        # Ollama accepts a JSON Schema dict directly as the `format`
        # field (since v0.5). For older Ollama, this gracefully degrades
        # to "JSON mode" (no schema enforcement) — we still validate
        # client-side.
        last_error: str | None = None
        attempt_prompt = prompt
        for attempt in range(max_retries + 1):
            response = self._call(
                attempt_prompt,
                model=model,
                temperature=0.0,
                max_tokens=None,
                timeout=timeout,
                format_param=schema,
            )
            try:
                obj = json.loads(response.text)
            except json.JSONDecodeError as e:
                last_error = f"Invalid JSON: {e}"
                attempt_prompt = self._repair_prompt(prompt, response.text, last_error)
                continue
            validation_error = self._validate_against_schema(obj, schema)
            if validation_error is None:
                return obj
            last_error = validation_error
            attempt_prompt = self._repair_prompt(prompt, response.text, last_error)
        raise LLMError(
            f"Ollama classify() failed after {max_retries + 1} attempts. "
            f"Last error: {last_error}"
        )

    # ── Internals ──
    def _call(
        self,
        prompt: str,
        *,
        model: str | None,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
        format_param: Any,
    ) -> LLMResponse:
        mdl = model or self.default_model
        body: dict[str, Any] = {
            "model": mdl,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            body["options"]["num_predict"] = max_tokens
        if format_param is not None:
            body["format"] = format_param

        url = f"{self.host}/api/generate"
        t0 = time.perf_counter()
        try:
            r = httpx.post(url, json=body, timeout=timeout)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama HTTP error: {e}") from e
        latency_ms = (time.perf_counter() - t0) * 1000
        data = r.json()

        text = data.get("response", "")
        prompt_tokens = data.get("prompt_eval_count")
        completion_tokens = data.get("eval_count")

        log.debug(
            "ollama.generate",
            model=mdl,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=round(latency_ms, 1),
        )

        return LLMResponse(
            text=text,
            model=mdl,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            raw=data,
        )

    # ── Minimal JSON-Schema validator ──
    # Full jsonschema would be heavier; we only need the fragment that
    # information.classifier will use: "object" with "required" and
    # per-property "type" and "enum".
    @staticmethod
    def _validate_against_schema(obj: Any, schema: dict[str, Any]) -> str | None:
        """Return None if valid, else an error message."""
        if schema.get("type") == "object":
            if not isinstance(obj, dict):
                return f"expected object, got {type(obj).__name__}"
            required = schema.get("required", [])
            for key in required:
                if key not in obj:
                    return f"missing required key: {key}"
            for key, sub in schema.get("properties", {}).items():
                if key not in obj:
                    continue
                err = OllamaProvider._validate_against_schema(obj[key], sub)
                if err:
                    return f"property {key!r}: {err}"
            return None
        expected_type = schema.get("type")
        if expected_type == "string":
            if not isinstance(obj, str):
                return f"expected string, got {type(obj).__name__}"
            enum = schema.get("enum")
            if enum is not None and obj not in enum:
                return f"value {obj!r} not in enum {enum}"
        elif expected_type == "number":
            if not isinstance(obj, (int, float)) or isinstance(obj, bool):
                return f"expected number, got {type(obj).__name__}"
            if "minimum" in schema and obj < schema["minimum"]:
                return f"value {obj} below minimum {schema['minimum']}"
            if "maximum" in schema and obj > schema["maximum"]:
                return f"value {obj} above maximum {schema['maximum']}"
        elif expected_type == "integer":
            if not isinstance(obj, int) or isinstance(obj, bool):
                return f"expected integer, got {type(obj).__name__}"
        elif expected_type == "boolean":
            if not isinstance(obj, bool):
                return f"expected boolean, got {type(obj).__name__}"
        elif expected_type == "array":
            if not isinstance(obj, list):
                return f"expected array, got {type(obj).__name__}"
            item_schema = schema.get("items")
            if item_schema:
                for i, item in enumerate(obj):
                    err = OllamaProvider._validate_against_schema(item, item_schema)
                    if err:
                        return f"item [{i}]: {err}"
        return None

    @staticmethod
    def _repair_prompt(original: str, bad_output: str, reason: str) -> str:
        return (
            f"{original}\n\n"
            f"--- Your previous response was rejected because: {reason} ---\n"
            f"--- Previous response: {bad_output[:300]} ---\n"
            f"Please respond again with VALID JSON only."
        )
