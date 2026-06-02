"""Per-(cluster, target) ensemble specification.

Stored in `var/models/ensembles.json` as a list of EnsembleSpec dicts.
Each spec names which registered models to combine + their weights,
plus the holdout validation metrics that justified the choice.

The decision engine looks up the latest active spec at inference time.

Schema::

    {
        "cluster_id": "US:ENERGY:LARGE",
        "target": "ret_fwd_21d",
        "members": [
            {"run_id": "2026-...", "model_kind": "lgbm"},
            {"run_id": "2026-...", "model_kind": "catboost"}
        ],
        "weights": [0.65, 0.35],
        "holdout_ic_mean": 0.241,
        "holdout_ic_std": 0.083,
        "holdout_hit_mean": 0.617,
        "holdout_windows": ["last_21d", "last_42d", "last_63d"],
        "decision_gate": "trade",         # trade | hold_only | ignore
        "validated_at": "2026-06-02T...",
        "supersedes": "<prev_spec_id>",  # for rollback
        "notes": "..."
    }
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from training.model_registry import get_models_root


@dataclass
class ModelRef:
    run_id: str
    model_kind: str


@dataclass
class EnsembleSpec:
    cluster_id: str
    target: str
    members: list[ModelRef]
    weights: list[float]
    holdout_ic_mean: float
    holdout_ic_std: float
    holdout_hit_mean: float
    holdout_windows: list[str]
    decision_gate: str = "trade"
    validated_at: str = ""
    supersedes: Optional[str] = None
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "target": self.target,
            "members": [asdict(m) for m in self.members],
            "weights": self.weights,
            "holdout_ic_mean": self.holdout_ic_mean,
            "holdout_ic_std": self.holdout_ic_std,
            "holdout_hit_mean": self.holdout_hit_mean,
            "holdout_windows": self.holdout_windows,
            "decision_gate": self.decision_gate,
            "validated_at": self.validated_at or datetime.now(timezone.utc).isoformat(),
            "supersedes": self.supersedes,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EnsembleSpec":
        return cls(
            cluster_id=d["cluster_id"],
            target=d["target"],
            members=[ModelRef(**m) for m in d["members"]],
            weights=list(d["weights"]),
            holdout_ic_mean=float(d["holdout_ic_mean"]),
            holdout_ic_std=float(d["holdout_ic_std"]),
            holdout_hit_mean=float(d["holdout_hit_mean"]),
            holdout_windows=list(d["holdout_windows"]),
            decision_gate=d.get("decision_gate", "trade"),
            validated_at=d.get("validated_at", ""),
            supersedes=d.get("supersedes"),
            notes=d.get("notes", ""),
        )


def _registry_path() -> Path:
    return get_models_root() / "ensembles.json"


def load_all() -> list[EnsembleSpec]:
    p = _registry_path()
    if not p.exists():
        return []
    try:
        return [EnsembleSpec.from_dict(d) for d in json.loads(p.read_text(encoding="utf-8"))]
    except Exception:
        return []


def find_active(cluster_id: str, target: str) -> Optional[EnsembleSpec]:
    """Return the most recent spec for (cluster, target)."""
    matches = [
        s for s in load_all()
        if s.cluster_id == cluster_id and s.target == target
    ]
    if not matches:
        return None
    return max(matches, key=lambda s: s.validated_at)


def append_spec(spec: EnsembleSpec) -> None:
    """Append spec to ensembles.json (append-only — old specs kept for rollback)."""
    p = _registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = load_all()
    # Mark the now-superseded spec
    prev = [s for s in existing if s.cluster_id == spec.cluster_id and s.target == spec.target]
    if prev:
        latest = max(prev, key=lambda s: s.validated_at)
        spec.supersedes = latest.validated_at
    existing.append(spec)
    p.write_text(
        json.dumps([s.as_dict() for s in existing], indent=2, default=str),
        encoding="utf-8",
    )


def list_specs_for_cluster(cluster_id: str) -> list[EnsembleSpec]:
    return [s for s in load_all() if s.cluster_id == cluster_id]
