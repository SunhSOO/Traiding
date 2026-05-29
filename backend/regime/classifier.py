"""Pure-function market-regime classifier.

Voting members (all weighted equally):

| Signal                  | RISK_ON when | RISK_OFF when |
|-------------------------|--------------|---------------|
| VIX level               | < 18         | > 28          |
| VIX trend (5d Δ)        | ≤ -1.5       | ≥ +2.0        |
| S&P/index trend vs 200d | price > SMA  | price < SMA   |
| 10Y - 2Y spread (proxy) | > 0.3        | < -0.2        |
| DXY trend (20d Δ %)     | < -1.0       | > +1.5        |

If a signal's input is missing it abstains (no vote in either
direction). The output label is the strict majority of votes; ties
or near-ties (max two votes apart) collapse to NEUTRAL. Confidence
is ``|majority - opposite| / total_votes``.

We deliberately keep the rule set short and hand-readable so the
operator can reason about why a label flipped. Adding a new signal
should require a unit test that pins its threshold."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Iterable, Optional


class RegimeLabel(str, enum.Enum):
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"


@dataclass(frozen=True)
class RegimeVote:
    signal: str               # short name e.g. "vix_level"
    vote: int                 # +1 RISK_ON, 0 abstain, -1 RISK_OFF
    detail: str               # one-line "why" for the audit / UI tooltip


@dataclass(frozen=True)
class RegimeReading:
    """One classifier output."""
    label: RegimeLabel
    confidence: float          # 0..1
    votes: list[RegimeVote] = field(default_factory=list)
    raw_inputs: dict = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────


def trend_vs_sma(
    series: list[float], *, window: int = 200,
) -> Optional[int]:
    """Return +1 if the latest value is above the trailing SMA, -1
    below, None if there aren't enough points."""
    if len(series) <= window:
        return None
    sma = sum(series[-(window + 1) : -1]) / window
    latest = series[-1]
    if sma == 0:
        return None
    if latest > sma:
        return 1
    if latest < sma:
        return -1
    return 0


def classify_regime(
    *,
    vix_level: Optional[float],
    vix_5d_delta: Optional[float],
    index_vs_sma200: Optional[int],
    yield_curve_spread: Optional[float] = None,
    dxy_20d_change_pct: Optional[float] = None,
) -> RegimeReading:
    """Combine signals into a regime label + confidence.

    All inputs are optional; missing ones abstain. The output stays
    NEUTRAL when fewer than 2 signals voted (insufficient evidence)
    or when the majority margin is too thin."""
    votes: list[RegimeVote] = []

    # 1. VIX level
    if vix_level is not None:
        if vix_level < 18:
            votes.append(RegimeVote("vix_level", +1, f"VIX {vix_level:.1f} < 18"))
        elif vix_level > 28:
            votes.append(RegimeVote("vix_level", -1, f"VIX {vix_level:.1f} > 28"))
        else:
            votes.append(RegimeVote("vix_level", 0, f"VIX {vix_level:.1f} in 18-28 band"))

    # 2. VIX 5-day delta
    if vix_5d_delta is not None:
        if vix_5d_delta <= -1.5:
            votes.append(RegimeVote("vix_trend", +1, f"VIX 5d Δ {vix_5d_delta:+.1f}"))
        elif vix_5d_delta >= 2.0:
            votes.append(RegimeVote("vix_trend", -1, f"VIX 5d Δ {vix_5d_delta:+.1f}"))
        else:
            votes.append(RegimeVote("vix_trend", 0, f"VIX 5d Δ {vix_5d_delta:+.1f} flat"))

    # 3. Index vs 200-day SMA (direction-only signal — already ±1 or 0)
    if index_vs_sma200 is not None:
        if index_vs_sma200 > 0:
            votes.append(RegimeVote("index_vs_sma200", +1, "index above 200d SMA"))
        elif index_vs_sma200 < 0:
            votes.append(RegimeVote("index_vs_sma200", -1, "index below 200d SMA"))
        else:
            votes.append(RegimeVote("index_vs_sma200", 0, "index on 200d SMA"))

    # 4. Yield-curve spread
    if yield_curve_spread is not None:
        if yield_curve_spread > 0.3:
            votes.append(RegimeVote("yield_curve", +1, f"10Y-2Y {yield_curve_spread:+.2f}"))
        elif yield_curve_spread < -0.2:
            votes.append(RegimeVote("yield_curve", -1, f"10Y-2Y {yield_curve_spread:+.2f} (inverted)"))
        else:
            votes.append(RegimeVote("yield_curve", 0, f"10Y-2Y {yield_curve_spread:+.2f} flat"))

    # 5. DXY 20-day change %
    if dxy_20d_change_pct is not None:
        if dxy_20d_change_pct < -1.0:
            votes.append(RegimeVote("dxy_trend", +1, f"DXY 20d {dxy_20d_change_pct:+.1f}% (weaker $)"))
        elif dxy_20d_change_pct > 1.5:
            votes.append(RegimeVote("dxy_trend", -1, f"DXY 20d {dxy_20d_change_pct:+.1f}% (stronger $)"))
        else:
            votes.append(RegimeVote("dxy_trend", 0, f"DXY 20d {dxy_20d_change_pct:+.1f}% flat"))

    directional = [v for v in votes if v.vote != 0]
    raw_inputs = {
        "vix_level": vix_level,
        "vix_5d_delta": vix_5d_delta,
        "index_vs_sma200": index_vs_sma200,
        "yield_curve_spread": yield_curve_spread,
        "dxy_20d_change_pct": dxy_20d_change_pct,
    }

    if len(directional) < 2:
        return RegimeReading(
            label=RegimeLabel.NEUTRAL, confidence=0.0,
            votes=votes, raw_inputs=raw_inputs,
        )

    pos = sum(1 for v in directional if v.vote > 0)
    neg = sum(1 for v in directional if v.vote < 0)
    total = pos + neg
    margin = abs(pos - neg)

    # Need at least a 2-vote majority to leave NEUTRAL — keeps the
    # output stable when one indicator flips around its threshold.
    if margin < 2:
        return RegimeReading(
            label=RegimeLabel.NEUTRAL,
            confidence=margin / total if total else 0.0,
            votes=votes, raw_inputs=raw_inputs,
        )

    label = RegimeLabel.RISK_ON if pos > neg else RegimeLabel.RISK_OFF
    confidence = margin / total
    return RegimeReading(
        label=label, confidence=confidence,
        votes=votes, raw_inputs=raw_inputs,
    )
