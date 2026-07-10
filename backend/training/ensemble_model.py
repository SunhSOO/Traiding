"""Ensemble rank model — LightGBM + Ridge, cross-sectional rank-average.

STATUS: implemented and rigorously tested, **NOT deployed** — kept as an opt-in
shadow-evaluation tool (train_production.py --ensemble). Production stays PURE
LightGBM for both markets.

Why not adopted (2026-07-10): a single 2022-06..2023-06 US split made ridge/ens
look dominant (ens top-decile EXCESS +0.85%/Sharpe 1.21 vs lgbm +0.27%/0.29),
which motivated this wrapper. But the robust check — a 16-fold walk-forward
(2020-2023, var/_analysis/wf_deep_US.csv) plus a 6-agent adversarial review —
found that edge does NOT survive:
  * gross edge is a wash: ens−lgbm mean excess +0.0010, MEDIAN −0.0071, 44% fold-win;
  * regime-lucky: 74% of the positive gap comes from 3 bull-rebound folds; drop
    them and the mean flips to −0.76pp;
  * bear-toxic: in the 2 bear folds the linear ridge leg's IC inverts (−0.24) and
    drags the blend to −1.73% vs lgbm +1.41% — worst exactly where drawdowns bite;
  * its only pro-story (vol/tail reduction) appears solely in a 2-month 2026 sweep
    and REVERSES on the walk-forward (ens more volatile, deeper worst fold).
Cost genuinely favors the blend (lower turnover), but that only amplifies a
within-noise gross edge. Ridge alone is the weakest selector. Path forward:
shadow-trade the blend and re-decide after 2024-2025 + a real drawdown regime.

Mechanics: this wrapper lets the ensemble occupy the bundle's ``rank_model``
slot so ProductionRecommender needs no change — it just calls ``.predict(X)``.
Features reaching ``.predict`` are ALREADY per-date cross-section z-scored (both
train_production.py and ProductionRecommender apply that transform). LightGBM
consumes them as-is; Ridge needs finite input, so ``_prep`` maps the residual
NaN/inf to 0.0 (== the neutral z-score) and clips tails. The output is the SUM
of the two models' cross-sectional ranks — order-preserving, which is all the
decision layer consumes (``rank_pct`` + sort by ``rank_score``).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class EnsembleRankModel:
    """Rank-average of a fitted LightGBM and a fitted Ridge. Picklable so it can
    be stored as bundle["rank_model"] and reloaded by ProductionRecommender."""

    def __init__(self, lgbm, ridge):
        self.lgbm = lgbm
        self.ridge = ridge

    @staticmethod
    def _prep(X) -> np.ndarray:
        """Ridge-safe view of already-z-scored features: finite, clipped, 0-filled.

        Used identically at fit-time (train_production) and predict-time so the
        Ridge branch sees the same transform. 0.0 == the neutral z-score, so an
        imputed value contributes nothing to the linear score."""
        Xd = pd.DataFrame(X).replace([np.inf, -np.inf], np.nan)
        return Xd.clip(-10.0, 10.0).fillna(0.0).to_numpy(dtype=float)

    def predict(self, X) -> np.ndarray:
        p_lgb = np.asarray(self.lgbm.predict(X), dtype=float)
        p_rid = np.asarray(self.ridge.predict(self._prep(X)), dtype=float)
        # Cross-sectional rank-average: scale-free, so the two heads combine on
        # equal footing regardless of their native output ranges.
        r_lgb = pd.Series(p_lgb).rank().to_numpy()
        r_rid = pd.Series(p_rid).rank().to_numpy()
        return r_lgb + r_rid
