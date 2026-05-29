"""Structured logging via structlog.

We always emit structured records (key=value or JSON). The format
switches between console (human-readable, colour) and JSON (production)
based on `Settings.log_format`. All trading decisions, risk checks, and
order outcomes go through here so they can be grepped, aggregated, and
shipped to log search later.

Usage:
    from core.logging import get_logger
    log = get_logger(__name__)
    log.info("order_placed", market="KR", ticker="005930", side="BUY", lot=0.1)

Do NOT use the stdlib `logging.getLogger` directly for app code — use
this helper so all records carry consistent shape.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from core.config import LogFormat, get_settings

_CONFIGURED = False


def configure_logging() -> None:
    """Idempotent: safe to call multiple times. First call wins."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level)

    # Tame chatty libraries (httpx, etc. are useful but spam INFO).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Common processors for stdlib + structlog records.
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.log_format == LogFormat.JSON:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Configure stdlib root logger so libraries that use logging.* are formatted too.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Configure structlog itself.
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a logger. Always call `configure_logging()` once at startup
    (FastAPI lifespan handles that)."""
    if not _CONFIGURED:
        # Lazy default so unit tests can use loggers without explicit setup.
        configure_logging()
    return structlog.get_logger(name)
