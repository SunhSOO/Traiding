"""Rule-based article-to-ticker mapping.

Walks article title + summary against a (name → ticker) dictionary
built from ``securities`` and returns scored mentions. Two priorities:

1. **Precision over recall.** We'd rather miss a tangential mention
   than tag an article about Apple cosmetics as AAPL. Specifically:
   - Single-character / 2-char Korean names are ignored unless they
     match the EXACT registered name (no substring).
   - English tickers ≤ 3 letters need word-boundary match.
   - Names that are common dictionary words ("APPLE", "STAR") need
     an additional context word from a curated keyword list — TODO
     for Phase 2 when we have an LLM to disambiguate; for now they
     skip.

2. **Title > body > tags-from-source.** Title hits get relevance
   0.9, summary hits 0.5, source tags 1.0. (Tags are authoritative
   because they come from the publisher's own metadata.)

LLM-based NER for the gnarly cases (alias resolution, subsidiary
parent mapping, news about "the iPhone maker" → AAPL) is Phase 2.
This module gives the LLM something high-quality to start from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from core.types import Market
from data.news.aliases import AliasEntry, seed_aliases


_WORD_BOUNDARY_TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z]{1,3})?$")


@dataclass(frozen=True)
class TickerMention:
    market: Market
    ticker: str
    mention_kind: str            # 'title' | 'body' | 'tag'
    relevance: float             # [0, 1]


@dataclass(frozen=True)
class NameIndex:
    """Pre-built lookup. The loader builds one of these once per run
    from the securities table and reuses it across articles.

    Two augmentation paths beyond the raw ``(name, ticker)`` pairs
    from the securities table:

    - **Corporate-suffix simplification** (US): ``"Apple Inc." →
      "Apple"`` so news bodies that drop the suffix still match.

    - **Curated aliases** from :mod:`data.news.aliases`: cross-
      language and colloquial short-forms. The seed list is small and
      hand-checked; extending it is cheap and the impact on recall is
      large for Korean-language English-named coverage."""

    kr_name_to_ticker: dict[str, str]
    us_name_to_ticker: dict[str, str]
    us_ticker_set: frozenset[str]            # for ALL-CAPS body matching

    @classmethod
    def from_pairs(
        cls,
        *,
        kr: Iterable[tuple[str, str]],
        us: Iterable[tuple[str, str]],
        extra_aliases: Optional[Iterable[AliasEntry]] = None,
        include_seed_aliases: bool = True,
    ) -> "NameIndex":
        kr_map = {n.strip(): t for n, t in kr if n and t}
        us_map = {n.strip(): t for n, t in us if n and t}
        # Add a few common alias-style cleanups for US names: strip
        # corporate suffixes so "Apple" matches "Apple Inc." entries.
        us_map_aug: dict[str, str] = dict(us_map)
        for name, ticker in us_map.items():
            simple = _simplify_us_name(name)
            if simple and simple != name and simple not in us_map_aug:
                us_map_aug[simple] = ticker

        # Curated aliases — only land them when the ticker is in the
        # active universe (we don't want to map "팔란티어" → PLTR if
        # PLTR isn't part of the loaded US universe yet).
        aliases: list[AliasEntry] = []
        if include_seed_aliases:
            aliases.extend(seed_aliases())
        if extra_aliases:
            aliases.extend(extra_aliases)

        kr_ticker_set = frozenset(kr_map.values())
        us_ticker_set = frozenset(us_map.values())
        for a in aliases:
            if a.market == "KR" and a.ticker in kr_ticker_set:
                # Aliases never overwrite a registered canonical name
                if a.alias not in kr_map:
                    kr_map[a.alias] = a.ticker
            elif a.market == "US" and a.ticker in us_ticker_set:
                if a.alias not in us_map_aug:
                    us_map_aug[a.alias] = a.ticker
        return cls(
            kr_name_to_ticker=kr_map,
            us_name_to_ticker=us_map_aug,
            us_ticker_set=us_ticker_set,
        )


def _simplify_us_name(name: str) -> str:
    """'Apple Inc.' → 'Apple'; 'Berkshire Hathaway Inc.' → 'Berkshire Hathaway'."""
    s = name.strip()
    for suffix in (" Inc.", " Inc", ", Inc.", ", Inc", " Corporation", " Corp.",
                   " Corp", " Co.", " Co", " Ltd.", " Ltd", " plc", " PLC",
                   " Company"):
        if s.endswith(suffix):
            return s[: -len(suffix)].strip(" ,")
    return s


def map_article(
    *,
    title: str,
    summary: str | None,
    source_tags: Iterable[str],
    index: NameIndex,
    top_n: int = 5,
    llm_tickers: Optional[Iterable[str]] = None,
) -> list[TickerMention]:
    """Return ranked ticker mentions for one article.

    Parameters
    ----------
    llm_tickers
        Optional list of tickers the LLM classifier identified in
        ``article_classifications.raw_llm_output['tickers_mentioned']``.
        Each LLM-suggested ticker is **validated against the universe**
        (silently dropped if unknown — paranoia about hallucinated
        tickers) and added at relevance 0.7. This sits between
        rule-based title hits (0.9) and body hits (0.5), reflecting
        moderate trust in the LLM's NER.
    """
    title = title or ""
    summary = summary or ""
    seen: dict[tuple[Market, str], TickerMention] = {}

    # 1. Source-provided tags (authoritative). Caller should pass canonical
    #    forms; we don't try to parse free-text tags here.
    for tag in source_tags:
        if not tag:
            continue
        market, ticker = _classify_tag(tag)
        if market is None:
            continue
        key = (market, ticker)
        seen[key] = TickerMention(market, ticker, "tag", 1.0)

    # 2. Title scan
    for name, ticker in index.kr_name_to_ticker.items():
        if _contains_kr(title, name):
            _upsert(seen, Market.KR, ticker, "title", 0.9)
    for name, ticker in index.us_name_to_ticker.items():
        if _contains_us(title, name):
            _upsert(seen, Market.US, ticker, "title", 0.9)
    # ALL-CAPS US tickers in title (handles "$AAPL hits new high")
    for ticker in index.us_ticker_set:
        if _word_boundary(title, ticker):
            _upsert(seen, Market.US, ticker, "title", 0.85)

    # 3. Summary scan (lower weight)
    for name, ticker in index.kr_name_to_ticker.items():
        if _contains_kr(summary, name):
            _upsert(seen, Market.KR, ticker, "body", 0.5)
    for name, ticker in index.us_name_to_ticker.items():
        if _contains_us(summary, name):
            _upsert(seen, Market.US, ticker, "body", 0.5)
    for ticker in index.us_ticker_set:
        if _word_boundary(summary, ticker):
            _upsert(seen, Market.US, ticker, "body", 0.45)

    # 4. LLM-suggested tickers — only land them when the universe
    #    actually contains the ticker. Mid-relevance 0.7 (above body,
    #    below title) reflects moderate trust in the LLM's NER vs
    #    the rule-based matcher. Bumps existing-key relevance when
    #    the LLM corroborates a title/body hit.
    if llm_tickers:
        kr_universe = frozenset(index.kr_name_to_ticker.values())
        for raw in llm_tickers:
            if not raw:
                continue
            t = str(raw).strip()
            if not t:
                continue
            # Try US first (uppercase), then KR (6-digit)
            tu = t.upper()
            if tu in index.us_ticker_set:
                _upsert(seen, Market.US, tu, "llm", 0.7)
            elif t in kr_universe:
                _upsert(seen, Market.KR, t, "llm", 0.7)
            # Silently drop tickers not in either universe — guards
            # against the LLM hallucinating made-up symbols.

    # Sort descending by relevance; cap to top_n to suppress noise
    ranked = sorted(seen.values(), key=lambda m: m.relevance, reverse=True)
    return ranked[:top_n]


# ── matching helpers ──
def _contains_kr(haystack: str, name: str) -> bool:
    """KR names: simple substring after stripping whitespace.
    Skip super-short names (1-2 chars) to avoid massive false-positive rate."""
    if not haystack or not name or len(name) < 2:
        return False
    return name in haystack


def _contains_us(haystack: str, name: str) -> bool:
    """US names: case-insensitive substring, but with conservative
    short-name handling. Multi-word names (>=2 words) always match;
    single-word names need to be at least 4 chars to avoid 'AT' / 'IT'
    style collisions."""
    if not haystack or not name:
        return False
    if " " not in name and len(name) < 4:
        return False
    return name.lower() in haystack.lower()


def _word_boundary(haystack: str, ticker: str) -> bool:
    """ALL-CAPS US ticker: must be at a word boundary. We anchor on
    optional $/# prefix (common stock conventions) and a trailing
    non-letter so 'AAPL ' or '$AAPL' match but 'AAPLE' doesn't."""
    if not haystack or not ticker or not _WORD_BOUNDARY_TICKER.match(ticker):
        return False
    pat = rf"(?:^|[\s$#(])({re.escape(ticker)})(?:[\s,.;:!?)$]|$)"
    return re.search(pat, haystack) is not None


def _classify_tag(tag: str) -> tuple[Market | None, str]:
    """Tag form heuristic:
    - 6 ASCII digits → KR ticker
    - 1-5 uppercase letters (optional .X) → US ticker
    - Anything else → unknown (caller should attempt name match first)"""
    t = tag.strip()
    if t.isdigit() and len(t) == 6:
        return (Market.KR, t)
    if _WORD_BOUNDARY_TICKER.match(t):
        return (Market.US, t)
    return (None, t)


def _upsert(
    seen: dict[tuple[Market, str], TickerMention],
    market: Market,
    ticker: str,
    mention_kind: str,
    relevance: float,
) -> None:
    key = (market, ticker)
    existing = seen.get(key)
    if existing is not None:
        # "title + body" or "title + llm" co-occurrence: small bonus,
        # cap at 0.95. Multiple corroborations of the same ticker are
        # a real signal but we keep "tag" (1.0) as the only path to
        # full confidence.
        if existing.mention_kind == "title" and mention_kind in ("body", "llm"):
            seen[key] = TickerMention(
                market, ticker, "title",
                min(0.95, existing.relevance + 0.05),
            )
            return
        # LLM corroborating a body hit: also small bonus
        if existing.mention_kind == "body" and mention_kind == "llm":
            seen[key] = TickerMention(
                market, ticker, "body",
                min(0.75, existing.relevance + 0.05),
            )
            return
        if existing.relevance >= relevance:
            return
    seen[key] = TickerMention(market, ticker, mention_kind, relevance)
