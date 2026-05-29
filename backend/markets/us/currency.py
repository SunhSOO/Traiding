"""US currency utilities. Trivial today (always USD)."""
from __future__ import annotations

CODE = "USD"
DECIMAL_PLACES = 2


def format_amount(value: float) -> str:
    """Localised USD display, e.g. 1234567.89 -> '$1,234,567.89'."""
    return f"${value:,.2f}"
