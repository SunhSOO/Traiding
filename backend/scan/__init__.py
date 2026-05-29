"""Signal scan — dry-run of the decision engine over latest scores.

Tells the operator what the next ``decisions.daily`` cron is likely
to produce, *without* triggering executions or writing audit rows.
Use cases:

- Pre-flight check before market open
- Spotting late-arriving scores that flip a signal
- Sanity-checking weight overrides
"""
from __future__ import annotations

from scan.engine import (
    ScanResult, ScanRequest,
    scan_signals,
)

__all__ = ["ScanResult", "ScanRequest", "scan_signals"]
