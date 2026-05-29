"""Training runner — orchestrate end-to-end retraining.

Steps:
1. For each market, assign clusters via ``clusterer.assign_clusters``.
   Persist to ``ticker_clusters`` so the decision engine can look up
   "what cluster does ticker X belong to today?" without recomputing.
2. Collect TrainSamples for the trailing ``window_days`` from
   ``module_scores`` and ``daily_prices``.
3. Group samples by cluster_id (using the freshly-computed assignments).
4. For each cluster with enough data, fit OLS via
   :func:`training.trainer.fit_cluster`.
5. Persist results to ``cluster_weights`` with a single ``learned_at``
   timestamp so the decision engine can query "give me the most-recent
   training run's weights".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from core.as_of import require_as_of
from core.logging import get_logger
from core.types import Market
from training.clusterer import assign_clusters
from training.labels import label_pairs
from training.registry import persist_assignments, persist_cluster_weights
from training.trainer import fit_cluster
from training.types import TrainResult, TrainSample
from training.walk_forward import generate_splits

log = get_logger(__name__)


@dataclass
class TrainRunReport:
    markets: list[str] = field(default_factory=list)
    samples_collected: int = 0
    clusters_attempted: int = 0
    clusters_trained: int = 0
    clusters_skipped_low_data: int = 0
    learned_at: Optional[datetime] = None
    cluster_details: list[dict] = field(default_factory=list)


@require_as_of
def run_training(
    session: Session,
    *,
    as_of: datetime,
    markets: tuple[Market, ...] = (Market.KR, Market.US),
    horizon_days: int = 5,
    window_days: int = 365,
    walk_forward: bool = True,
    model_version: str = "ols-v1",
) -> TrainRunReport:
    """Full retraining pass.

    Parameters
    ----------
    horizon_days : int
        Forward-return horizon used as the regression target.
    window_days : int
        Trailing window of score data to use. 365 = one year.
    walk_forward : bool
        If True, also compute the walk-forward R² as a sanity metric.
    """
    report = TrainRunReport()
    report.learned_at = datetime.now(UTC)
    score_window_start = as_of - timedelta(days=window_days)
    score_window_end = as_of - timedelta(days=horizon_days)  # avoid look-ahead

    # 1. Re-cluster every market and persist
    assignments_by_key: dict[tuple[str, str], str] = {}
    for m in markets:
        assigns = assign_clusters(session, market=m.value, as_of=as_of)
        persist_assignments(session, assigns, assigned_at=report.learned_at)
        for a in assigns:
            assignments_by_key[(a.market, a.ticker)] = a.cluster_id
        report.markets.append(m.value)

    # 2. Build samples across all markets (group later by cluster)
    all_samples: list[TrainSample] = []
    for m in markets:
        tickers = [t for (mk, t) in assignments_by_key if mk == m.value]
        samples = label_pairs(
            session, market=m.value, tickers=tickers,
            score_window_start=score_window_start,
            score_window_end=score_window_end,
            horizon_days=horizon_days,
        )
        all_samples.extend(samples)
    report.samples_collected = len(all_samples)
    log.info("training.samples_collected", n=len(all_samples))

    if not all_samples:
        log.warning("training.no_samples", note="cluster_weights table left untouched")
        return report

    # 3. Group by cluster
    by_cluster: dict[str, list[TrainSample]] = {}
    for s in all_samples:
        cid = assignments_by_key.get((s.market, s.ticker))
        if cid is None:
            continue
        by_cluster.setdefault(cid, []).append(s)

    # 4. Fit each cluster
    splits = generate_splits(
        start=score_window_start, end=score_window_end,
    ) if walk_forward else []

    results: list[TrainResult] = []
    for cluster_id, samples in by_cluster.items():
        report.clusters_attempted += 1
        result = fit_cluster(
            samples, cluster_id=cluster_id, model_version=model_version,
            walk_forward_splits=splits,
        )
        if result is None:
            report.clusters_skipped_low_data += 1
            report.cluster_details.append({
                "cluster_id": cluster_id, "skipped": True,
                "n_samples": len(samples),
            })
            continue
        results.append(result)
        report.clusters_trained += 1
        report.cluster_details.append({
            "cluster_id": cluster_id, "skipped": False,
            "n_samples": result.metrics.n_samples,
            "n_tickers": result.metrics.n_tickers,
            "r2": result.metrics.r2_in_sample,
            "r2_wf": result.metrics.r2_walk_forward,
            "hit_rate": result.metrics.hit_rate,
            "weights": {
                "F": result.w_fundamental,
                "T": result.w_technical,
                "I": result.w_information,
            },
        })

    # 5. Persist atomically (one learned_at for all clusters)
    if results:
        persist_cluster_weights(session, results, learned_at=report.learned_at)

    log.info(
        "training.run_done",
        markets=report.markets,
        samples=report.samples_collected,
        trained=report.clusters_trained,
        skipped=report.clusters_skipped_low_data,
    )
    return report
