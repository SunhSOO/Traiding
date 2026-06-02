"""LSTM sequence trainer (PyTorch).

Why a separate file: the data shape is fundamentally different —
trees consume (n_samples, n_features), while LSTM consumes
(n_samples, sequence_length, n_features). The feature pipeline
output (rows of features) is reshaped here into rolling sequences.

Architecture (configurable):
  Input (B, T, F)  →  LSTM(hidden=128, layers=2, dropout=0.3)
  → last hidden    →  Linear(64) + ReLU + Dropout(0.3)
  → Linear(1)      →  prediction (forward return or rank)

Training:
  - Adam, lr 1e-3, weight_decay 1e-4
  - early stopping on val RMSE
  - same date-grouped CV / embargo as multi_trainer
  - sequence_length = 30 days (configurable)

Honest scope: NOT hyperparameter-tuned. Defaults are sane but Optuna
search would likely add another +0.01–0.03 R². GPU recommended.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from training.lgbm_trainer import (
    FoldMetrics,
    TrainResultLGB as TrainResult,
    _date_grouped_splits,
    _hit_rate,
    _ic,
    _r2,
)


def _make_sequences(
    df: pd.DataFrame, feature_cols: list[str], target_col: str,
    seq_len: int = 30,
):
    """Per-ticker rolling windows. Returns (X, y, dates, tickers)."""
    import numpy as _np
    X_list, y_list, d_list, t_list = [], [], [], []
    for ticker, group in df.sort_values(["ticker", "date"]).groupby("ticker"):
        feat = group[feature_cols].astype(float).fillna(0.0).values
        y = group[target_col].astype(float).values
        dates = group["date"].values
        if len(group) < seq_len + 1:
            continue
        for i in range(seq_len, len(group)):
            X_list.append(feat[i - seq_len:i])
            y_list.append(y[i])
            d_list.append(dates[i])
            t_list.append(ticker)
    if not X_list:
        return None, None, None, None
    return (
        _np.stack(X_list).astype("float32"),
        _np.array(y_list, dtype="float32"),
        pd.Series(d_list),
        pd.Series(t_list),
    )


def train_lstm(
    df: pd.DataFrame,
    *,
    feature_cols: list[str],
    target_col: str,
    cluster_id: str = "__global__",
    seq_len: int = 30,
    hidden_dim: int = 128,
    num_layers: int = 2,
    dropout: float = 0.3,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    batch_size: int = 1024,
    n_epochs: int = 30,
    patience: int = 5,
    n_splits: int = 4,
    embargo_days: int = 21,
    device: Optional[str] = None,
) -> Optional[TrainResult]:
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError:
        print("  [lstm] PyTorch not installed; aborting")
        return None

    if df.empty:
        return None
    df = df.dropna(subset=[target_col]).copy()
    if len(df) < 1000:
        return None

    # NaN handling per row (LSTM can't ingest NaN). Use ticker-level
    # forward-fill then zero-fill — preserves time-series shape.
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    df[feature_cols] = df.groupby("ticker")[feature_cols].ffill().fillna(0.0)

    X, y, dates, tickers = _make_sequences(df, feature_cols, target_col, seq_len)
    if X is None:
        return None
    n_samples = len(X)
    n_features = X.shape[2]
    print(f"  [lstm] sequences: n={n_samples}, seq_len={seq_len}, n_features={n_features}")

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"  [lstm] device={device}")

    splits = _date_grouped_splits(dates, n_splits, embargo_days)
    if not splits:
        return None

    cv_metrics: list[FoldMetrics] = []
    oof_pred = np.full(n_samples, np.nan, dtype="float32")
    final_model = None

    class _Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=n_features, hidden_size=hidden_dim,
                num_layers=num_layers, batch_first=True, dropout=dropout,
            )
            self.fc = nn.Sequential(
                nn.Linear(hidden_dim, 64),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(64, 1),
            )

        def forward(self, x):
            out, _ = self.lstm(x)
            return self.fc(out[:, -1, :]).squeeze(-1)

    for fold_idx, (train_idx, test_idx) in enumerate(splits):
        if len(train_idx) < 200 or len(test_idx) < 50:
            continue
        X_tr = torch.from_numpy(X[train_idx]).to(device)
        y_tr = torch.from_numpy(y[train_idx]).to(device)
        X_te = torch.from_numpy(X[test_idx]).to(device)
        y_te = torch.from_numpy(y[test_idx]).to(device)

        ds_tr = TensorDataset(X_tr, y_tr)
        dl_tr = DataLoader(ds_tr, batch_size=batch_size, shuffle=True)

        model = _Net().to(device)
        opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
        loss_fn = nn.MSELoss()

        best_val = float("inf")
        best_state = None
        bad_epochs = 0
        for epoch in range(n_epochs):
            model.train()
            for xb, yb in dl_tr:
                opt.zero_grad()
                pred = model(xb)
                loss = loss_fn(pred, yb)
                loss.backward()
                opt.step()
            # Eval on test
            model.eval()
            with torch.no_grad():
                val_pred = model(X_te).cpu().numpy()
            val_rmse = float(np.sqrt(np.mean((y[test_idx] - val_pred) ** 2)))
            if val_rmse < best_val - 1e-6:
                best_val = val_rmse
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
                bad_epochs = 0
            else:
                bad_epochs += 1
                if bad_epochs >= patience:
                    break

        if best_state:
            model.load_state_dict(best_state)
        model.eval()
        with torch.no_grad():
            pred_te = model(X_te).cpu().numpy()
            pred_tr = model(X_tr).cpu().numpy()
        oof_pred[test_idx] = pred_te
        final_model = model

        cv_metrics.append(FoldMetrics(
            fold=fold_idx,
            n_train=len(train_idx), n_test=len(test_idx),
            rmse_train=float(np.sqrt(np.mean((y[train_idx] - pred_tr) ** 2))),
            rmse_test=best_val,
            r2_train=_r2(y[train_idx], pred_tr),
            r2_test=_r2(y[test_idx], pred_te),
            hit_rate_test=_hit_rate(y[test_idx], pred_te),
            ic_spearman_test=_ic(y[test_idx], pred_te),
        ))

    if not cv_metrics:
        return None

    mask = ~np.isnan(oof_pred)
    y_oof = y[mask]
    p_oof = oof_pred[mask]

    return TrainResult(
        target=target_col,
        cluster_id=cluster_id,
        booster=final_model,
        feature_names=feature_cols,
        cv_metrics=cv_metrics,
        final_r2_oof=_r2(y_oof, p_oof),
        final_hit_rate_oof=_hit_rate(y_oof, p_oof),
        final_ic_oof=_ic(y_oof, p_oof),
        final_rmse_oof=float(np.sqrt(np.mean((y_oof - p_oof) ** 2))),
        n_samples=n_samples,
        feature_importance={},  # LSTM doesn't give feature gain
        notes=f"lstm seq_len={seq_len} hidden={hidden_dim} layers={num_layers}",
    )
