"""LLM provider abstract base.

Two interfaces:

- ``generate(prompt)``: free-form text completion. Returns the raw
  string plus accounting metadata (tokens, latency, model, provider).
- ``classify(prompt, schema)``: forces the model into a JSON shape
  that matches ``schema``. Used by the information module to extract
  (event_type, sentiment, impact, horizon) etc. without parsing free
  text afterwards. Implementations must retry with stricter prompts
  or schema repair until the output validates — partial dicts are
  considered failures.

All providers must respect a ``timeout`` and surface their own
provider-specific errors as :class:`LLMError` so call sites only
need one except clause.

We do NOT expose any "system prompt" knob in the base interface
because the information module always uses task-specific prompts
that bundle their own preamble. Provider-specific defaults are
hidden inside concrete classes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class LLMError(RuntimeError):
    """Wraps any provider error so call sites have a single error type."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens


class LLMProvider(ABC):
    """Concrete providers implement the two methods below and a
    ``health_check`` for ops dashboards."""

    name: str = "abstract"

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout: float = 60.0,
    ) -> LLMResponse:
        """Return a free-form completion. ``temperature=0`` by default
        because almost all our calls want deterministic extraction."""

    @abstractmethod
    def classify(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        model: str | None = None,
        max_retries: int = 2,
        timeout: float = 90.0,
    ) -> dict[str, Any]:
        """Return a JSON object matching ``schema`` (a subset of
        JSON Schema). Providers must validate before returning;
        invalid output should be retried up to ``max_retries`` with
        a "your previous output was invalid, here's why" prompt
        before raising :class:`LLMError`.
        """

    @abstractmethod
    def health_check(self, *, timeout: float = 5.0) -> bool:
        """Cheap reachability probe. Return True if the provider is
        usable right now, False otherwise. Must NOT raise."""
