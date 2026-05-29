"""Walk-forward cross-validation split generation.

A walk-forward split is the time-series analog of k-fold CV:
- Train on [t-W, t)
- Test on [t, t+T)
- Slide t forward by `step_days` and repeat

This is the only sound CV scheme for trading models because random
shuffling of time-series samples leaks future into past.

We expose ``generate_splits`` as a pure function so the trainer can
call it with arbitrary date ranges. The trainer never invents its
own splitter.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator

from training.types import WalkForwardSplit


def generate_splits(
    *,
    start: datetime,
    end: datetime,
    train_window_days: int = 180,
    test_window_days: int = 20,
    step_days: int = 20,
    min_train_days: int = 60,
) -> list[WalkForwardSplit]:
    """Generate non-overlapping (in test set) walk-forward splits.

    Parameters
    ----------
    start, end : datetime
        Outer bounds. The first test window starts at
        ``start + min_train_days``; the last one ends at or before
        ``end``.
    train_window_days : int
        Width of the training window. Use a smaller value when you
        want the model to adapt to recent regime changes.
    test_window_days : int
        Width of the test window. Smaller = finer evaluation, more
        compute.
    step_days : int
        How far to slide between successive test windows. Equal to
        ``test_window_days`` for non-overlapping tests; smaller for
        overlapping (more samples evaluated, but correlated).
    """
    if start >= end:
        return []
    if test_window_days <= 0 or step_days <= 0:
        raise ValueError("test_window_days and step_days must be > 0")

    splits: list[WalkForwardSplit] = []
    cursor = start + timedelta(days=min_train_days)
    while cursor + timedelta(days=test_window_days) <= end:
        train_start = max(start, cursor - timedelta(days=train_window_days))
        train_end = cursor
        test_start = cursor
        test_end = cursor + timedelta(days=test_window_days)
        splits.append(WalkForwardSplit(
            train_start=train_start, train_end=train_end,
            test_start=test_start, test_end=test_end,
        ))
        cursor += timedelta(days=step_days)
    return splits


def iter_splits(*args, **kwargs) -> Iterator[WalkForwardSplit]:
    """Generator-style alias for convenience in long-running training loops."""
    yield from generate_splits(*args, **kwargs)
