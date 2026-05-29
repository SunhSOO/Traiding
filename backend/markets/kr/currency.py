"""KR currency utilities. Trivial today (always KRW); kept as its
own module so future expansion (e.g. KOSPI USD share class) has a
clear home."""
from __future__ import annotations

CODE = "KRW"
DECIMAL_PLACES = 0   # KRW shows no fractional units in UI by convention


def format_amount(value: float) -> str:
    """Localised KRW display, e.g. 1234567.89 -> '₩1,234,568'."""
    return f"₩{round(value):,}"
