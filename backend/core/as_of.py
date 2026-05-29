"""Look-ahead bias guard.

The single most important invariant of this system is:

    A signal computed at time T must not depend on any data that
    became available after time T.

If this is violated even once, backtest numbers become fiction and
the model that learned from them will fail in live trading. We make
the rule **structural** rather than a convention by funnelling every
data-read through a function that requires an explicit `as_of`
argument, and by providing a context that pins "now" inside training
and backtest runs.

This module gives you three tools:

1. ``AsOfContext``: a context manager that pins a clock; nested
   code can call ``now()`` and get the pinned value. Live trading
   uses ``datetime.now(UTC)``; backtests pin to the bar timestamp.

2. ``now()``: returns the pinned timestamp, or wall-clock UTC when
   nothing is pinned.

3. ``@require_as_of``: a decorator for repository functions. Forces
   the caller to pass `as_of: datetime`, rejects None, rejects
   future timestamps relative to wall-clock (cheap sanity check),
   and rejects naive datetimes (must be timezone-aware to prevent
   subtle UTC vs local confusion).

The intent is that EVERY function that pulls historical prices,
financials, disclosures, news, or any other data MUST be decorated
with ``@require_as_of`` and use the `as_of` arg to filter. A code
review failing this rule should block the PR.
"""
from __future__ import annotations

import contextvars
import functools
import inspect
from datetime import UTC, datetime
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_pinned_now: contextvars.ContextVar[datetime | None] = contextvars.ContextVar(
    "woonam_as_of_pinned_now", default=None
)


class AsOfError(ValueError):
    """Raised when a function is called in a way that violates the
    look-ahead-bias invariant."""


class AsOfContext:
    """Pin "now" to a specific instant for the duration of a block.

    Use in backtests so that any code which falls back to ``now()``
    sees the historical timestamp, not wall-clock.

        with AsOfContext(bar_close_ts):
            decision = run_signal_pipeline(symbol)
    """

    def __init__(self, pinned: datetime):
        if pinned.tzinfo is None:
            raise AsOfError("AsOfContext pinned timestamp must be timezone-aware.")
        self._pinned = pinned
        self._token: contextvars.Token[datetime | None] | None = None

    def __enter__(self) -> "AsOfContext":
        self._token = _pinned_now.set(self._pinned)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._token is not None:
            _pinned_now.reset(self._token)
            self._token = None


def now() -> datetime:
    """Return the currently-pinned `as_of` if inside an
    :class:`AsOfContext`, otherwise wall-clock UTC."""
    pinned = _pinned_now.get()
    return pinned if pinned is not None else datetime.now(UTC)


def _validate(as_of: Any) -> datetime:
    if as_of is None:
        raise AsOfError("`as_of` is required and cannot be None.")
    if not isinstance(as_of, datetime):
        raise AsOfError(f"`as_of` must be datetime, got {type(as_of).__name__}.")
    if as_of.tzinfo is None:
        raise AsOfError(
            "`as_of` must be timezone-aware (tzinfo is None). "
            "Use datetime.now(UTC) or attach a tzinfo before calling."
        )
    # Cheap sanity check against wall clock. We allow some skew because
    # different machines may have slightly different clocks.
    wall = datetime.now(UTC)
    if (as_of - wall).total_seconds() > 60:
        raise AsOfError(
            f"`as_of` is more than 60s in the future "
            f"(as_of={as_of.isoformat()}, wall={wall.isoformat()}). "
            "This is almost certainly a bug; data may not yet exist."
        )
    return as_of


def require_as_of(func: F) -> F:
    """Decorator: enforces that the wrapped function receives a valid
    `as_of` keyword argument (or positional, if declared so).

    Use on every repository function that returns historical data.

        @require_as_of
        def get_close_price(symbol: Symbol, *, as_of: datetime) -> float:
            ...

    The decorator inspects the signature to ensure `as_of` is declared,
    and validates the runtime value (non-None, timezone-aware, not in
    the future).
    """
    sig = inspect.signature(func)
    if "as_of" not in sig.parameters:
        raise TypeError(
            f"@require_as_of: function {func.__qualname__} must declare "
            f"an `as_of` parameter."
        )

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        bound = sig.bind_partial(*args, **kwargs)
        as_of_value = bound.arguments.get("as_of")
        _validate(as_of_value)
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
