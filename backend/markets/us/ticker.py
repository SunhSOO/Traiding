"""US ticker normalization.

US tickers are 1-5 (occasionally 6) uppercase letters, optionally with:
- Class suffix: 'BRK.B' (NYSE), 'BRK-B' (CRSP), 'BRK/B' (legacy). We
  normalize to dot form (e.g. 'BRK.B').
- Exchange suffix from Yahoo: '.N' (NYSE), '.OQ' (NASDAQ), '.US'.
  We strip these.
- Whitespace/case variants — we strip and uppercase.

Tickers with numeric components (e.g. some preferred shares) are
allowed since they appear in S&P 500 / NASDAQ-100.
"""
from __future__ import annotations

import re

_VALID_RE = re.compile(r"^[A-Z]{1,6}(\.[A-Z]{1,3})?$")
_EXCHANGE_SUFFIX_RE = re.compile(r"\.(N|OQ|US|O)$", re.IGNORECASE)


def normalize_us_ticker(raw: str) -> str:
    if raw is None:
        raise ValueError("ticker is required")
    s = raw.strip().upper()
    if not s:
        raise ValueError("ticker is required")

    # Strip Yahoo/Reuters exchange suffix
    s = _EXCHANGE_SUFFIX_RE.sub("", s)

    # Normalize class separators to '.'
    s = s.replace("-", ".").replace("/", ".")

    if not _VALID_RE.match(s):
        raise ValueError(f"Not a valid US ticker: {raw!r}")
    return s


def is_valid_us_ticker(raw: str) -> bool:
    try:
        normalize_us_ticker(raw)
        return True
    except ValueError:
        return False
