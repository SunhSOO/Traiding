"""Optuna LGBM tuning with an OOS-IC objective (not return).

Tuning to RETURN overfits to the period (the campaign's recurring trap). We tune
to the walk-forward OOS rank-IC instead — the metric that tracks genuine
selection skill — and only accept the tuned config if it beats the default IC on
BOTH markets out of sample. mn label + per-date normalize + Blitz (current best).

Usage: uv run python scripts/opt_lab.py --market US --trials 40
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import optuna
from alpha_lab import run_experiment, PERIOD  # reuse harness

optuna.logging.set_verbosity(optuna.logging.WARNING)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US")
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--step", type=int, default=42)
    a = ap.parse_args()
    df = pd.read_parquet(Path(f"var/_bt_period_{a.market}_{PERIOD}.parquet"))
    df["date"] = pd.to_datetime(df["date"])
    base_cfg = dict(label="mn", reselect=True, normalize=True, step=a.step)

    def ic_of(model_params, seed):
        m = run_experiment(df, a.market, seed=seed, model_params=model_params, **base_cfg)
        return m.get("ic") if m and m.get("ic") is not None else -1.0

    # default baseline IC (2-seed mean) for reference
    base_ic = np.mean([ic_of(None, s) for s in (42, 1)])
    print(f"[opt] {a.market} default OOS-IC (2seed) = {base_ic:+.4f}", flush=True)

    def objective(trial):
        mp = dict(
            num_leaves=trial.suggest_int("num_leaves", 15, 63),
            learning_rate=trial.suggest_float("learning_rate", 0.01, 0.08, log=True),
            n_estimators=trial.suggest_int("n_estimators", 200, 600, step=50),
            min_child_samples=trial.suggest_int("min_child_samples", 50, 300),
            subsample=trial.suggest_float("subsample", 0.5, 0.9),
            colsample_bytree=trial.suggest_float("colsample_bytree", 0.4, 0.9),
            reg_lambda=trial.suggest_float("reg_lambda", 1.0, 20.0),
        )
        return ic_of(mp, 42)   # single-seed during search (speed)

    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=a.trials, show_progress_bar=False)
    best = study.best_params
    # re-evaluate best with 2 seeds (honest)
    tuned_ic = np.mean([ic_of(best, s) for s in (42, 1)])
    print(f"\n[opt] {a.market} best params: {best}", flush=True)
    print(f"[opt] tuned OOS-IC (2seed) = {tuned_ic:+.4f}  vs default {base_ic:+.4f}  "
          f"=> {'BEATS' if tuned_ic > base_ic + 0.001 else 'no gain'}", flush=True)


if __name__ == "__main__":
    main()
