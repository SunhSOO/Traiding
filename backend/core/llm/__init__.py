"""LLM provider abstraction + concrete providers.

The information-analysis module (Phase 2) calls an LLM to classify
news/disclosure text into structured signals. We hide the provider
behind a uniform interface so we can:

- Default to local Ollama on the operator's GPU (free, private).
- Fall back to Groq (free tier, fast) when local is overloaded.
- Use Gemini (free tier) for redundancy / A/B comparison.

The interface is intentionally narrow: ``generate`` for free-form
text and ``classify`` for JSON-schema-constrained extraction. We
deliberately avoid exposing provider-specific knobs in the base
interface so swapping providers never requires changes in call sites.
"""
from __future__ import annotations

from core.llm.base import LLMError, LLMProvider, LLMResponse
from core.llm.ollama_provider import OllamaProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LLMError",
    "OllamaProvider",
    "get_default_provider",
]


def get_default_provider() -> LLMProvider:
    """Return the highest-priority LLM provider that is configured.

    Order: Ollama (local) → Groq → Gemini. We never silently fall
    back across providers at runtime — callers must wrap multi-provider
    failover themselves and log the switch (so auditability isn't lost).
    """
    from core.config import get_settings

    settings = get_settings()
    if settings.has_ollama:
        return OllamaProvider()
    raise LLMError(
        "No LLM provider configured. Set OLLAMA_HOST or add a Groq/Gemini "
        "provider (not yet implemented). See backend/.env.example."
    )
