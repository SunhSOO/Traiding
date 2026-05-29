"""Information-analysis module.

Reads ``news_articles`` + ``disclosures`` → uses an LLM (Ollama
qwen2.5:14b by default) to extract structured event/sentiment/impact
classifications → applies source trust + time-decay weighting → emits
a per-ticker score in [-100, +100].

Pipeline:
    raw articles → classifier (LLM) → article_classifications
                                    → augmented ticker mentions
    classified mentions → score → module_scores (module='I')

The classifier is the most expensive step (GPU-bound). The runner
processes articles in priority order (rule-mapped tickers first)
and skips already-classified articles by (article_id, model_version).
"""
from __future__ import annotations

from information.score import score_information
from information.types import ArticleClassification, EventType, Horizon, Impact, Sentiment

__all__ = [
    "ArticleClassification",
    "EventType", "Sentiment", "Impact", "Horizon",
    "score_information",
]
