"""LLM-based article classifier.

Sends a prompt to the configured LLM provider asking for a strict
JSON object matching :data:`SCHEMA`. Provider.classify() validates
the response against the schema and retries on malformed output, so
this module's job is just prompt construction + result mapping.

Hallucination guard:
- Tickers returned by the model are checked against a caller-supplied
  whitelist (the active securities universe). Unknown tickers are
  dropped silently.
- We REQUIRE the model to include a `summary` that cites at least one
  phrase from the original article; if it doesn't, confidence is
  capped at 0.5.
- Articles with no recognised ticker after filtering are still
  classified (event_type / sentiment / impact may matter for a sector
  or macro view) but the runner won't write ticker-specific mentions.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from core.llm import LLMError, LLMProvider, get_default_provider
from core.logging import get_logger
from information.types import (
    ArticleClassification, EventType, Horizon, Impact, Sentiment,
)

log = get_logger(__name__)


SCHEMA: dict = {
    "type": "object",
    "required": ["event_type", "sentiment", "impact", "horizon", "confidence", "summary"],
    "properties": {
        "event_type": {"type": "string", "enum": [e.value for e in EventType]},
        "sentiment": {"type": "string", "enum": [s.value for s in Sentiment]},
        "impact": {"type": "string", "enum": [i.value for i in Impact]},
        "horizon": {"type": "string", "enum": [h.value for h in Horizon]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string"},
        "tickers_mentioned": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


PROMPT_TEMPLATE = """You are a financial news analyst classifying one article for an
automated trading system. Output STRICT JSON ONLY matching the schema.

Article (in {language}):
TITLE: {title}
SUMMARY: {summary}
PUBLISHER: {publisher}
PUBLISHED: {published_iso}

Instructions:
- event_type: pick ONE of {event_types}
- sentiment: POSITIVE / NEUTRAL / NEGATIVE about the named companies' stock
  prices, not the publisher's editorial tone.
- impact: HIGH if material info likely to move the stock; MEDIUM for
  modest moves; LOW for routine coverage.
- horizon: how long the effect should persist. INTRADAY for fleeting
  reactions; SHORT_TERM for days to weeks; MEDIUM_TERM for months;
  LONG_TERM for quarters or more.
- confidence: 0.0 to 1.0 based on how well-supported your call is.
- summary: 1-2 sentence neutral paraphrase. Cite at least one specific
  phrase or number from the article.
- tickers_mentioned: list of ticker symbols (KR 6-digit numeric like
  "005930" or US uppercase letters like "AAPL", "BRK.B") that the
  article is materially about. Only include tickers you are confident
  appear in the article. If none, return [].

Return JSON only, no commentary."""


_TICKER_KR_RE = re.compile(r"^\d{6}$")
_TICKER_US_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z]{1,3})?$")


def classify_article(
    *,
    title: str,
    summary: str,
    publisher: Optional[str] = None,
    language: str = "ko",
    published_iso: str = "",
    provider: Optional[LLMProvider] = None,
    ticker_whitelist: Optional[Iterable[str]] = None,
) -> ArticleClassification:
    """Classify one article. Returns ArticleClassification (always —
    on LLM failure we return a low-confidence OTHER/NEUTRAL placeholder
    so the caller can still persist a row and skip re-classification)."""
    provider = provider or get_default_provider()
    prompt = PROMPT_TEMPLATE.format(
        title=title or "(no title)",
        summary=summary or "(no summary)",
        publisher=publisher or "(unknown)",
        language=language,
        published_iso=published_iso,
        event_types=", ".join(e.value for e in EventType),
    )

    try:
        raw = provider.classify(prompt, SCHEMA, max_retries=2)
    except LLMError as e:
        log.warning("info.classify_failed", error=str(e))
        return _fallback_classification(reason=str(e))

    try:
        event = EventType(raw["event_type"])
        sentiment = Sentiment(raw["sentiment"])
        impact = Impact(raw["impact"])
        horizon = Horizon(raw["horizon"])
        confidence = float(raw["confidence"])
    except (KeyError, ValueError) as e:
        log.warning("info.parse_failed", error=str(e), raw=raw)
        return _fallback_classification(reason=f"unparseable enum: {e}")

    # Hallucination guard 1: ticker whitelist filter
    tickers = tuple(_filter_tickers(raw.get("tickers_mentioned", []), ticker_whitelist))

    # Hallucination guard 2: summary must reference the input text
    summary_text = (raw.get("summary") or "").strip()
    if not _summary_grounded(summary_text, title=title, body=summary):
        confidence = min(confidence, 0.5)

    return ArticleClassification(
        event_type=event,
        sentiment=sentiment,
        impact=impact,
        horizon=horizon,
        confidence=max(0.0, min(1.0, confidence)),
        summary=summary_text[:1024],
        tickers_mentioned=tickers,
    )


def _filter_tickers(
    raw_tickers: list,
    whitelist: Optional[Iterable[str]],
) -> list[str]:
    """Drop tickers that don't match syntax or aren't in the active universe."""
    valid: list[str] = []
    allowed = set(whitelist) if whitelist is not None else None
    for t in raw_tickers:
        if not isinstance(t, str):
            continue
        t = t.strip().upper()
        if not (_TICKER_KR_RE.match(t) or _TICKER_US_RE.match(t)):
            continue
        if allowed is not None and t not in allowed:
            continue
        valid.append(t)
    return valid


def _summary_grounded(summary: str, *, title: str, body: str) -> bool:
    """Heuristic: at least one non-trivial 3-word phrase from summary
    must also appear in the source title or body. Cheap but catches
    obvious hallucinations."""
    if not summary:
        return False
    haystack = f"{title or ''} {body or ''}"
    if len(haystack.strip()) < 20:
        return True  # too little source to ground against; skip the guard
    words = re.findall(r"\w+", summary)
    if len(words) < 4:
        return True   # short summary; nothing to verify
    for i in range(len(words) - 2):
        phrase = " ".join(words[i : i + 3])
        if len(phrase) >= 8 and phrase in haystack:
            return True
    return False


def _fallback_classification(*, reason: str) -> ArticleClassification:
    return ArticleClassification(
        event_type=EventType.OTHER,
        sentiment=Sentiment.NEUTRAL,
        impact=Impact.LOW,
        horizon=Horizon.SHORT_TERM,
        confidence=0.0,
        summary=f"[classifier-failed: {reason}]",
        tickers_mentioned=(),
    )
