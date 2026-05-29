"""Macro time-series ingestion — FX, interest rates, indices, VIX.

Three free sources, one normalized `macro_series` table:

| Source     | Use                                 | Free        |
|------------|-------------------------------------|-------------|
| FRED       | US rates, USD index, VIX, treasury  | Yes (key)   |
| BOK ECOS   | KR rates, KRW FX, KOSPI index       | Yes (key)   |
| FDR        | Backfill / fallback                 | Yes         |

Series codes follow our internal scheme — e.g. ``FX_USDKRW``,
``RATE_US_FFR``, ``IDX_KOSPI``, ``VIX``. The mapping from internal
code → external source key lives in the adapter modules.
"""
from __future__ import annotations

from data.macro.loader import sync_macro_series

__all__ = ["sync_macro_series"]
