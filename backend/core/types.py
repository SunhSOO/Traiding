"""SQLAlchemy-free shared enums / value types.

Anything that wants to know "what is a Market" but doesn't need
ORM/DB plumbing imports from here. This keeps modules like
``markets`` (pure logic) testable without the heavy DB stack.

Models in ``core.models`` re-export from this module for backward
compat so external callers can keep doing ``from core.models import Market``.
"""
from __future__ import annotations

import enum


class Market(str, enum.Enum):
    """Trading venue market identifier.

    KR / US are the only first-class equity markets in this system.
    Non-equity venues (e.g. MT5 for FX/gold) use their own broker
    adapter and do NOT use this enum.
    """
    KR = "KR"
    US = "US"
