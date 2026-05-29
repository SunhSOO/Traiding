"""Module performance attribution — pure math, no DB.

Given a set of (decision_module_scores, forward_return) samples, ask:
*which module's score best predicted the realised return?*

Two complementary measures per module:

1. **Pearson correlation** ``r(score, return)`` — how linearly the
   module's signal tracked outcomes. Robust to scale; sensitive to
   outliers; sign is meaningful (negative r = the module was
   anti-predictive).

2. **Sign accuracy** — fraction of samples where the module's
   sign(score) matched sign(return). Captures direction even when
   magnitude is uncorrelated. Computed only on samples with non-zero
   score; HOLD-territory scores are excluded.

The combined "attribution share" is the absolute Pearson coefficient
normalised across the three modules. It answers "of the
predictability we have, what share comes from F vs T vs I?" — a 0..1
number per module summing to 1.

The math here is small and explicit. No NumPy dependency — fits in
the same easy-to-vendor footprint as ``brokers.paper_equity``."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class AttributionSample:
    """One (decision, outcome) pair.

    ``module_scores`` is a ``{"F"|"T"|"I": float}`` dict. Missing keys
    mean the module didn't score at decision time; the sample is
    excluded from that module's stats but counted for the others.

    ``forward_return`` is the realised P&L for the trade, expressed as
    a fraction of the *entry value* (so it's scale-invariant across
    KRW/USD and across position sizes)."""
    module_scores: dict[str, float]
    forward_return: float


@dataclass(frozen=True)
class ModuleAttribution:
    module: str
    n_samples: int
    mean_score: float
    mean_return: float
    pearson_r: Optional[float]    # None when n < 2 or score variance is 0
    sign_accuracy: Optional[float]  # None when n < 1
    attribution_share: float       # 0..1, see compute_attribution


@dataclass(frozen=True)
class AttributionResult:
    n_samples_total: int
    modules: list[ModuleAttribution]


# ──────────────────────────────────────────────────────────────────────


def compute_attribution(
    samples: Iterable[AttributionSample],
    *,
    modules: tuple[str, ...] = ("F", "T", "I"),
) -> AttributionResult:
    """Reduce the sample stream into per-module stats.

    Empty inputs return zeros. Modules with insufficient variance
    (constant score) get ``pearson_r=None`` and contribute 0 to the
    attribution share."""
    by_module: dict[str, list[tuple[float, float]]] = {m: [] for m in modules}
    n_total = 0
    for s in samples:
        n_total += 1
        for m in modules:
            score = s.module_scores.get(m)
            if score is None:
                continue
            by_module[m].append((float(score), float(s.forward_return)))

    raw: list[tuple[str, int, float, float, Optional[float], Optional[float]]] = []
    for m in modules:
        pairs = by_module[m]
        n = len(pairs)
        if n == 0:
            raw.append((m, 0, 0.0, 0.0, None, None))
            continue
        scores = [p[0] for p in pairs]
        returns = [p[1] for p in pairs]
        mean_s = sum(scores) / n
        mean_r = sum(returns) / n
        r = _pearson(scores, returns)
        acc = _sign_accuracy(pairs)
        raw.append((m, n, mean_s, mean_r, r, acc))

    # Attribution share — normalise |r| across modules.
    total_abs_r = sum(abs(r) for _, _, _, _, r, _ in raw if r is not None) or 1.0
    out: list[ModuleAttribution] = []
    for m, n, ms, mr, r, acc in raw:
        share = (abs(r) / total_abs_r) if r is not None else 0.0
        out.append(ModuleAttribution(
            module=m, n_samples=n,
            mean_score=ms, mean_return=mr,
            pearson_r=r, sign_accuracy=acc,
            attribution_share=share,
        ))
    return AttributionResult(n_samples_total=n_total, modules=out)


# ──────────────────────────────────────────────────────────────────────


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return num / (sx * sy)


def _sign_accuracy(pairs: list[tuple[float, float]]) -> Optional[float]:
    if not pairs:
        return None
    hits = 0
    counted = 0
    for s, r in pairs:
        if s == 0 or r == 0:
            continue
        counted += 1
        if (s > 0) == (r > 0):
            hits += 1
    if counted == 0:
        return None
    return hits / counted
