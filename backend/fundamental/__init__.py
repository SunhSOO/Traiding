"""Fundamental-analysis module.

Reads ``financial_facts`` (canonical concept rows from DART/EDGAR) +
``daily_prices`` (for price-based ratios like P/E, P/B) and produces
a per-ticker score in [-100, +100].

Pipeline:
    raw concepts → ratios → within-sector percentile → score
"""
from __future__ import annotations
