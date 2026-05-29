"""Korean ticker normalization.

KRX tickers are 6-digit numeric strings (e.g. '005930' for Samsung
Electronics). Variations we accept and normalize:

- '5930'      → '005930'   (zero-pad to 6 digits)
- '005930.KS' → '005930'   (strip Yahoo Finance suffixes)
- '005930.KQ' → '005930'
- ' 005930 '  → '005930'
- 'A005930'   → '005930'   (Korean broker A-prefix)

Rejected:
- empty string
- anything that doesn't reduce to 1-6 ASCII digits
"""
from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"^\d{1,6}$")
_SUFFIX_RE = re.compile(r"\.(KS|KQ|KOSPI|KOSDAQ)$", re.IGNORECASE)
_PREFIX_RE = re.compile(r"^A(\d{6})$")


def normalize_kr_ticker(raw: str) -> str:
    """Return the canonical 6-digit form. Raises ValueError if input
    cannot be parsed as a KRX ticker."""
    if raw is None:
        raise ValueError("ticker is required")
    s = raw.strip()
    if not s:
        raise ValueError("ticker is required")

    s = _SUFFIX_RE.sub("", s)
    m = _PREFIX_RE.match(s)
    if m:
        s = m.group(1)

    if not _DIGITS_RE.match(s):
        raise ValueError(f"Not a valid KR ticker: {raw!r}")
    return s.zfill(6)


def is_valid_kr_ticker(raw: str) -> bool:
    try:
        normalize_kr_ticker(raw)
        return True
    except ValueError:
        return False
