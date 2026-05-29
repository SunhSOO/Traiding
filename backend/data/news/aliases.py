"""Curated alias dictionary for company name disambiguation.

Two purposes:

1. **Cross-language aliases.** Korean news often uses an English
   brand name (Samsung Electronics, Hyundai Motor) while the
   ``securities.name`` row holds the Korean version. Without an alias
   map the substring matcher misses every English mention. Likewise,
   US tickers sometimes appear in Korean articles as ``애플`` rather
   than the company's official English name.

2. **Short-form aliases.** ``"삼전"`` → 005930, ``"하이닉스"`` →
   000660. These are colloquial but extremely common in retail news.

We deliberately keep this list short and high-precision. Adding a new
alias should require checking that it isn't a common dictionary word
or another company's name. False positives here cost the system
real money (the Information score gets pumped by irrelevant news);
a few false negatives cost only recall.

The seed below is hand-curated. A future expansion path:

- Phase 2.x: pull aliases from DART corp_code metadata for KR
- Phase 2.x: use SEC company tickers JSON for US legal names

All aliases are stored as ``(alias, market, ticker)`` triples; the
loader joins them with the live ``NameIndex`` at startup.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AliasEntry:
    alias: str
    market: str       # "KR" | "US"
    ticker: str


# Seed list — KR top-mention companies.
# Format: (alias, ticker). Market is implied by entry order; we split
# explicitly to avoid magic strings.
_KR_ALIASES: list[tuple[str, str]] = [
    # Samsung group — frequent English & Korean shorthand
    ("Samsung Electronics", "005930"),
    ("Samsung Elec", "005930"),
    ("삼전", "005930"),
    ("Samsung Biologics", "207940"),
    ("삼바", "207940"),
    ("Samsung SDI", "006400"),

    # SK group
    ("SK Hynix", "000660"),
    ("하이닉스", "000660"),
    ("SK 하이닉스", "000660"),

    # Hyundai group
    ("Hyundai Motor", "005380"),
    ("현대차", "005380"),
    ("Hyundai Mobis", "012330"),
    ("Kia", "000270"),
    ("기아차", "000270"),

    # LG group
    ("LG Energy Solution", "373220"),
    ("LG Chem", "051910"),
    ("LG Electronics", "066570"),

    # Naver / Kakao
    ("Naver", "035420"),
    ("NAVER", "035420"),
    ("Kakao", "035720"),
    ("카카오", "035720"),

    # POSCO
    ("POSCO", "005490"),
    ("포스코", "005490"),
    ("POSCO Holdings", "005490"),

    # KB / Shinhan / 하나
    ("KB Financial", "105560"),
    ("Shinhan Financial", "055550"),
    ("Hana Financial", "086790"),

    # Celltrion
    ("Celltrion", "068270"),
    ("셀트리온", "068270"),

    # 한미 / 한미반도체 등
    ("Hanmi Semiconductor", "042700"),
    ("한미반도체", "042700"),
]


# Seed list — US top-mention companies. Mostly Korean-language aliases
# of well-known US tickers since the US-side English matcher already
# handles the obvious ones via ``_simplify_us_name``.
_US_ALIASES: list[tuple[str, str]] = [
    ("애플", "AAPL"),
    ("Apple Inc", "AAPL"),

    ("마이크로소프트", "MSFT"),
    ("MS", "MSFT"),    # high-collision; the matcher's word-boundary
                       # logic + 4-char floor keeps this safe-ish.

    ("엔비디아", "NVDA"),

    ("구글", "GOOGL"),
    ("알파벳", "GOOGL"),
    ("Alphabet", "GOOGL"),

    ("아마존", "AMZN"),

    ("메타", "META"),
    ("페이스북", "META"),

    ("테슬라", "TSLA"),
    ("Tesla Inc", "TSLA"),

    ("브로드컴", "AVGO"),

    ("팔란티어", "PLTR"),

    ("AMD", "AMD"),    # length-3 — caller relies on word-boundary
    ("인텔", "INTC"),

    ("버크셔", "BRK.B"),
    ("Berkshire", "BRK.B"),
]


def seed_aliases() -> list[AliasEntry]:
    """Return the bundled alias list as ``AliasEntry`` records."""
    out: list[AliasEntry] = []
    for alias, ticker in _KR_ALIASES:
        out.append(AliasEntry(alias=alias, market="KR", ticker=ticker))
    for alias, ticker in _US_ALIASES:
        out.append(AliasEntry(alias=alias, market="US", ticker=ticker))
    return out
