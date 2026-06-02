"""Model persistence + MLOps-style registry.

Directory layout::

    var/models/
        registry.json                    # global index of every run
        runs/
            <run_id>/                    # e.g. 2026-06-01T08-30_a3f4
                manifest.json            # run metadata (params, dates, features)
                <model_kind>/
                    <cluster_id>/
                        <target>.bin     # serialized booster / state_dict
                        metrics.json     # CV metrics + feature importance

``run_id`` = ``{ISO_minute}_{short_hash}`` so runs sort chronologically
and stay unique even with concurrent jobs.

Why JSON registry instead of DB:
- Models reload at decision time, no SQL lag
- Easy diff with git for drift forensics
- DB schema for model metadata would couple training to migrations

Drift detection workflow (Phase 6+):
1. New data arrives → recompute features
2. Predict with all registered models of latest run
3. Compare prediction distribution vs. previous run
4. If KS-test p < 0.05 on output distribution → trigger retrain
5. New retrain auto-registers + becomes "latest"; old run stays for rollback
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


_DEFAULT_ROOT = Path("var") / "models"


def get_models_root() -> Path:
    root = Path(os.environ.get("MODEL_DIR", str(_DEFAULT_ROOT)))
    (root / "runs").mkdir(parents=True, exist_ok=True)
    return root


def new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
    short = uuid.uuid4().hex[:4]
    return f"{ts}_{short}"


# ──────────────────────────────────────────────────────────────────────


@dataclass
class ModelArtifact:
    """One persisted model — a (run_id, model_kind, cluster_id, target) tuple."""
    run_id: str
    model_kind: str
    cluster_id: str
    target: str
    model_path: str       # relative to models_root
    metrics_path: str
    n_samples: int = 0
    final_r2_oof: float = 0.0
    final_hit_rate_oof: float = 0.0
    final_ic_oof: float = 0.0
    final_rmse_oof: float = 0.0
    feature_names: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────


def _safe(cluster_id: str) -> str:
    return cluster_id.replace(":", "_").replace("/", "_").replace(" ", "_")


def _booster_save(booster: Any, model_kind: str, path: Path) -> None:
    """Persist a fitted model. Each library has its own format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if model_kind == "lgbm":
        booster.save_model(str(path))
    elif model_kind == "xgb":
        booster.save_model(str(path))
    elif model_kind == "catboost":
        booster.save_model(str(path), format="cbm")
    elif model_kind == "ridge":
        import joblib
        joblib.dump(booster, str(path))
    elif model_kind == "lstm":
        import torch
        torch.save(booster.state_dict(), str(path))
    else:
        raise ValueError(f"unknown model_kind={model_kind}")


def _booster_load(model_kind: str, path: Path) -> Any:
    """Re-hydrate a saved model. Caller must already have config to build the
    architecture for LSTM."""
    if model_kind == "lgbm":
        import lightgbm as lgb
        return lgb.Booster(model_file=str(path))
    elif model_kind == "xgb":
        import xgboost as xgb
        booster = xgb.Booster()
        booster.load_model(str(path))
        return booster
    elif model_kind == "catboost":
        from catboost import CatBoostRegressor
        m = CatBoostRegressor()
        m.load_model(str(path), format="cbm")
        return m
    elif model_kind == "ridge":
        import joblib
        return joblib.load(str(path))
    elif model_kind == "lstm":
        # Caller supplies an empty model instance; we load state_dict externally.
        import torch
        return torch.load(str(path), map_location="cpu")
    else:
        raise ValueError(f"unknown model_kind={model_kind}")


_EXT_BY_KIND = {
    "lgbm": "txt", "xgb": "json", "catboost": "cbm",
    "ridge": "joblib", "lstm": "pt",
}


def save_model(
    booster: Any,
    *,
    run_id: str,
    model_kind: str,
    cluster_id: str,
    target: str,
    metrics: dict,
    feature_names: list[str],
    params: Optional[dict] = None,
) -> ModelArtifact:
    """Persist a fitted model + a sidecar metrics.json. Returns the artifact."""
    root = get_models_root()
    run_dir = root / "runs" / run_id / model_kind / _safe(cluster_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    ext = _EXT_BY_KIND.get(model_kind, "bin")
    model_path = run_dir / f"{target}.{ext}"
    metrics_path = run_dir / f"{target}.metrics.json"

    _booster_save(booster, model_kind, model_path)
    metrics_path.write_text(
        json.dumps({
            **metrics,
            "feature_names": feature_names,
            "params": params or {},
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2, default=str),
        encoding="utf-8",
    )

    artifact = ModelArtifact(
        run_id=run_id,
        model_kind=model_kind,
        cluster_id=cluster_id,
        target=target,
        model_path=str(model_path.relative_to(root)),
        metrics_path=str(metrics_path.relative_to(root)),
        n_samples=int(metrics.get("n_samples", 0)),
        final_r2_oof=float(metrics.get("final_r2_oof", 0.0)),
        final_hit_rate_oof=float(metrics.get("final_hit_rate_oof", 0.0)),
        final_ic_oof=float(metrics.get("final_ic_oof", 0.0)),
        final_rmse_oof=float(metrics.get("final_rmse_oof", 0.0)),
        feature_names=feature_names,
        params=params or {},
    )
    _append_registry(artifact)
    return artifact


def write_run_manifest(
    run_id: str, *, manifest: dict,
) -> Path:
    root = get_models_root()
    run_root = root / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    p = run_root / "manifest.json"
    p.write_text(
        json.dumps({
            **manifest,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2, default=str),
        encoding="utf-8",
    )
    return p


# ──────────────────────────────────────────────────────────────────────


def _registry_path() -> Path:
    return get_models_root() / "registry.json"


def load_registry() -> list[dict]:
    p = _registry_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_registry(artifact: ModelArtifact) -> None:
    p = _registry_path()
    entries = load_registry()
    entries.append(asdict(artifact))
    p.write_text(json.dumps(entries, indent=2, default=str), encoding="utf-8")


def find_best(
    *, target: str, cluster_id: str,
    metric: str = "final_ic_oof",
    model_kind: Optional[str] = None,
) -> Optional[dict]:
    """Return the registry entry with the best `metric` for the given key.

    For drift / re-train flows the consumer wants the *current best* — that's
    the latest run with the highest IC for that (target, cluster).
    """
    entries = load_registry()
    filtered = [
        e for e in entries
        if e["target"] == target and e["cluster_id"] == cluster_id
        and (model_kind is None or e["model_kind"] == model_kind)
    ]
    if not filtered:
        return None
    return max(filtered, key=lambda e: e.get(metric, 0.0))


def list_run(run_id: str) -> list[dict]:
    return [e for e in load_registry() if e["run_id"] == run_id]
