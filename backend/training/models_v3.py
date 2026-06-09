"""Wave 3 — Comprehensive model zoo.

Adds 10 new model architectures on top of the existing LightGBM / XGBoost /
CatBoost / Ridge / LSTM:

  1. StackingEnsemble    - sklearn meta-learner over LGBM/XGB/CatBoost/LSTM
  2. GaussianProcessReg  - sklearn GPR (RBF kernel)
  3. NBEATSModel         - darts N-BEATS (univariate per-ticker)
  4. TFTModel            - pytorch-forecasting Temporal Fusion Transformer
  5. PatchTSTModel       - darts PatchTST (transformer with patching)
  6. CausalForestModel   - econml HonestForest for treatment-effect proxy
  7. BNNModel            - torch BNN via MC Dropout (uncertainty estimate)
  8. ChronosForecaster   - Amazon Chronos pretrained zero-shot
  9. TimesFMForecaster   - Google TimesFM pretrained zero-shot
 10. FinBERTScorer       - HuggingFace FinBERT for text sentiment

All wrappers share the same interface where applicable:
  - .fit(X_train, y_train, X_val=None, y_val=None) -> self
  - .predict(X) -> np.ndarray
  - .available() -> bool (True if dependencies present)
  - .get_feature_importance() -> Optional[dict]

Heavy deps are imported lazily inside .fit() so the module loads fast.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────────────────
# 1. Stacking Ensemble
# ──────────────────────────────────────────────────────────────────────


class StackingEnsemble:
    """Meta-learner over base model predictions.

    Base models must be pre-trained; pass their oof predictions to .fit().
    Default meta-learner: Ridge (low variance). Optionally ElasticNet / LGBM.
    """
    name = "stacking"

    def __init__(self, meta: str = "ridge", alpha: float = 1.0):
        self.meta_kind = meta
        self.alpha = alpha
        self.model = None

    def available(self) -> bool:
        return True

    def fit(self, oof_preds: pd.DataFrame, y: pd.Series, *, sample_weight=None):
        from sklearn.linear_model import Ridge, ElasticNet
        from sklearn.ensemble import GradientBoostingRegressor
        if self.meta_kind == "ridge":
            self.model = Ridge(alpha=self.alpha, positive=False)
        elif self.meta_kind == "elasticnet":
            self.model = ElasticNet(alpha=self.alpha, l1_ratio=0.3, positive=False)
        elif self.meta_kind == "gbm":
            self.model = GradientBoostingRegressor(n_estimators=50, max_depth=3)
        else:
            raise ValueError(self.meta_kind)
        X = oof_preds.fillna(0).values
        if sample_weight is not None:
            self.model.fit(X, y.values, sample_weight=sample_weight)
        else:
            self.model.fit(X, y.values)
        return self

    def predict(self, base_preds: pd.DataFrame) -> np.ndarray:
        X = base_preds.fillna(0).values
        return self.model.predict(X)

    def get_feature_importance(self):
        if hasattr(self.model, "coef_"):
            return dict(zip(self.feature_names_in_, self.model.coef_)) \
                if hasattr(self.model, "feature_names_in_") else None
        if hasattr(self.model, "feature_importances_"):
            return dict(enumerate(self.model.feature_importances_))
        return None


# ──────────────────────────────────────────────────────────────────────
# 2. Gaussian Process Regression
# ──────────────────────────────────────────────────────────────────────


class GaussianProcessReg:
    name = "gp"

    def __init__(self, length_scale: float = 1.0, alpha: float = 1e-2):
        self.length_scale = length_scale
        self.alpha = alpha
        self.model = None
        self.feature_means = None
        self.feature_stds = None

    def available(self) -> bool:
        try:
            import sklearn.gaussian_process   # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, X: pd.DataFrame, y: pd.Series, X_val=None, y_val=None):
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import RBF, WhiteKernel, ConstantKernel as C
        # GPR is O(N^3) — sub-sample if too large
        X_arr = X.fillna(0).values.astype(np.float32)
        y_arr = y.values.astype(np.float32)
        n_max = 3000
        if len(X_arr) > n_max:
            idx = np.random.RandomState(42).choice(len(X_arr), n_max, replace=False)
            X_arr, y_arr = X_arr[idx], y_arr[idx]
        # Standardize for kernel
        self.feature_means = X_arr.mean(axis=0)
        self.feature_stds = X_arr.std(axis=0) + 1e-8
        X_norm = (X_arr - self.feature_means) / self.feature_stds
        kernel = C(1.0) * RBF(length_scale=self.length_scale) + WhiteKernel(noise_level=self.alpha)
        self.model = GaussianProcessRegressor(kernel=kernel, alpha=self.alpha,
                                                normalize_y=True, n_restarts_optimizer=2)
        self.model.fit(X_norm, y_arr)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        X_arr = X.fillna(0).values.astype(np.float32)
        X_norm = (X_arr - self.feature_means) / self.feature_stds
        return self.model.predict(X_norm)

    def predict_with_std(self, X: pd.DataFrame):
        X_arr = X.fillna(0).values.astype(np.float32)
        X_norm = (X_arr - self.feature_means) / self.feature_stds
        return self.model.predict(X_norm, return_std=True)


# ──────────────────────────────────────────────────────────────────────
# 3. N-BEATS (darts)
# ──────────────────────────────────────────────────────────────────────


class NBEATSModel:
    name = "nbeats"

    def __init__(self, input_chunk_length: int = 30, output_chunk_length: int = 5,
                  n_epochs: int = 20):
        self.input_chunk_length = input_chunk_length
        self.output_chunk_length = output_chunk_length
        self.n_epochs = n_epochs
        self.model = None

    def available(self) -> bool:
        try:
            import darts   # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, ts_series_list: list, X_val=None, y_val=None):
        """ts_series_list: list of darts TimeSeries (one per ticker)."""
        if not self.available():
            raise ImportError("darts not installed")
        from darts.models import NBEATSModel as _NBEATS
        self.model = _NBEATS(
            input_chunk_length=self.input_chunk_length,
            output_chunk_length=self.output_chunk_length,
            n_epochs=self.n_epochs,
            random_state=42,
            pl_trainer_kwargs={"accelerator": "auto"},
        )
        self.model.fit(ts_series_list)
        return self

    def predict(self, ts_series_list: list, n: int = 5):
        return self.model.predict(n=n, series=ts_series_list)


# ──────────────────────────────────────────────────────────────────────
# 4. Temporal Fusion Transformer (pytorch-forecasting)
# ──────────────────────────────────────────────────────────────────────


class TFTModel:
    name = "tft"

    def __init__(self, input_size: int = 30, max_prediction_length: int = 5,
                  n_epochs: int = 10, hidden_size: int = 16,
                  attention_head_size: int = 1):
        self.input_size = input_size
        self.max_prediction_length = max_prediction_length
        self.n_epochs = n_epochs
        self.hidden_size = hidden_size
        self.attention_head_size = attention_head_size
        self.model = None

    def available(self) -> bool:
        try:
            import pytorch_forecasting   # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, df: pd.DataFrame, target_col: str, group_col: str = "ticker",
             time_col: str = "time_idx", feature_cols: Optional[list] = None,
             val_df: Optional[pd.DataFrame] = None):
        if not self.available():
            raise ImportError("pytorch-forecasting not installed")
        import torch
        from pytorch_forecasting import TimeSeriesDataSet, TemporalFusionTransformer
        from pytorch_forecasting.metrics import QuantileLoss
        import lightning.pytorch as pl
        max_encoder_length = self.input_size
        training_cutoff = df[time_col].max() - self.max_prediction_length
        feature_cols = feature_cols or [c for c in df.columns
            if c not in (target_col, group_col, time_col)]
        training = TimeSeriesDataSet(
            df[df[time_col] <= training_cutoff],
            time_idx=time_col, target=target_col, group_ids=[group_col],
            min_encoder_length=max_encoder_length // 2,
            max_encoder_length=max_encoder_length,
            min_prediction_length=1,
            max_prediction_length=self.max_prediction_length,
            time_varying_known_reals=[time_col],
            time_varying_unknown_reals=[target_col] + feature_cols,
        )
        validation = TimeSeriesDataSet.from_dataset(training, df, predict=True, stop_randomization=True)
        train_dl = training.to_dataloader(train=True, batch_size=128, num_workers=0)
        val_dl = validation.to_dataloader(train=False, batch_size=128, num_workers=0)
        self.model = TemporalFusionTransformer.from_dataset(
            training,
            learning_rate=1e-3,
            hidden_size=self.hidden_size,
            attention_head_size=self.attention_head_size,
            dropout=0.1, hidden_continuous_size=8,
            loss=QuantileLoss(),
        )
        trainer = pl.Trainer(max_epochs=self.n_epochs, accelerator="auto", devices=1,
                              enable_progress_bar=False)
        trainer.fit(self.model, train_dataloaders=train_dl, val_dataloaders=val_dl)
        self.train_dataset = training
        self.val_dataloader = val_dl
        return self

    def predict(self, df: pd.DataFrame):
        from pytorch_forecasting import TimeSeriesDataSet
        ds = TimeSeriesDataSet.from_dataset(self.train_dataset, df,
                                              predict=True, stop_randomization=True)
        dl = ds.to_dataloader(train=False, batch_size=128, num_workers=0)
        preds = self.model.predict(dl)
        return preds.cpu().numpy() if hasattr(preds, "cpu") else preds


# ──────────────────────────────────────────────────────────────────────
# 5. PatchTST (darts)
# ──────────────────────────────────────────────────────────────────────


class PatchTSTModel:
    name = "patchtst"

    def __init__(self, input_chunk_length: int = 30,
                  output_chunk_length: int = 5, n_epochs: int = 15):
        self.input_chunk_length = input_chunk_length
        self.output_chunk_length = output_chunk_length
        self.n_epochs = n_epochs
        self.model = None

    def available(self) -> bool:
        try:
            import darts   # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, ts_series_list: list, X_val=None, y_val=None):
        if not self.available():
            raise ImportError("darts not installed")
        try:
            from darts.models import PatchTSTModel as _PatchTST
        except ImportError:
            # Older darts may not have PatchTST; fall back to TransformerModel
            from darts.models import TransformerModel as _PatchTST
        self.model = _PatchTST(
            input_chunk_length=self.input_chunk_length,
            output_chunk_length=self.output_chunk_length,
            n_epochs=self.n_epochs,
            random_state=42,
            pl_trainer_kwargs={"accelerator": "auto"},
        )
        self.model.fit(ts_series_list)
        return self

    def predict(self, ts_series_list: list, n: int = 5):
        return self.model.predict(n=n, series=ts_series_list)


# ──────────────────────────────────────────────────────────────────────
# 6. Causal Forest (econml HonestForest)
# ──────────────────────────────────────────────────────────────────────


class CausalForestModel:
    """Used to estimate treatment effect of e.g. 'positive earnings surprise'
    on subsequent returns. Less standard for pure prediction but adds an
    orthogonal signal from causal-inference perspective."""
    name = "causal_forest"

    def __init__(self, n_estimators: int = 100, max_depth: int = 6):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.model = None

    def available(self) -> bool:
        try:
            import econml   # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, X: pd.DataFrame, y: pd.Series, T: pd.Series,
             W: Optional[pd.DataFrame] = None, X_val=None):
        """T: treatment indicator (binary). X: features. W: controls."""
        if not self.available():
            raise ImportError("econml not installed")
        from econml.dml import CausalForestDML
        self.model = CausalForestDML(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            discrete_treatment=True,
            random_state=42,
        )
        W_arr = W.fillna(0).values if W is not None else None
        self.model.fit(y.values, T.values, X=X.fillna(0).values, W=W_arr)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Returns conditional treatment effect estimate."""
        return self.model.effect(X.fillna(0).values)


# ──────────────────────────────────────────────────────────────────────
# 7. Bayesian Neural Net via MC Dropout
# ──────────────────────────────────────────────────────────────────────


class BNNModel:
    """BNN approximation via MC Dropout (Gal & Ghahramani 2016).
    Returns mean + uncertainty over T forward passes."""
    name = "bnn_mcdropout"

    def __init__(self, hidden_dim: int = 64, n_epochs: int = 30,
                  dropout: float = 0.3, mc_samples: int = 30):
        self.hidden_dim = hidden_dim
        self.n_epochs = n_epochs
        self.dropout = dropout
        self.mc_samples = mc_samples
        self.model = None
        self.feature_means = None
        self.feature_stds = None

    def available(self) -> bool:
        try:
            import torch   # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, X: pd.DataFrame, y: pd.Series, X_val=None, y_val=None):
        import torch
        import torch.nn as nn
        X_arr = X.fillna(0).values.astype(np.float32)
        y_arr = y.values.astype(np.float32).reshape(-1, 1)
        self.feature_means = X_arr.mean(axis=0)
        self.feature_stds = X_arr.std(axis=0) + 1e-8
        X_norm = (X_arr - self.feature_means) / self.feature_stds
        n_features = X_norm.shape[1]

        class MCDropoutNet(nn.Module):
            def __init__(self_, n_in, n_hidden, dropout):
                super().__init__()
                self_.net = nn.Sequential(
                    nn.Linear(n_in, n_hidden), nn.ReLU(), nn.Dropout(dropout),
                    nn.Linear(n_hidden, n_hidden), nn.ReLU(), nn.Dropout(dropout),
                    nn.Linear(n_hidden, 1),
                )
            def forward(self_, x):
                return self_.net(x)

        self.model = MCDropoutNet(n_features, self.hidden_dim, self.dropout)
        opt = torch.optim.Adam(self.model.parameters(), lr=1e-3)
        loss_fn = nn.MSELoss()
        X_t = torch.from_numpy(X_norm)
        y_t = torch.from_numpy(y_arr)
        for epoch in range(self.n_epochs):
            self.model.train()
            opt.zero_grad()
            pred = self.model(X_t)
            loss = loss_fn(pred, y_t)
            loss.backward()
            opt.step()
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        import torch
        X_arr = X.fillna(0).values.astype(np.float32)
        X_norm = (X_arr - self.feature_means) / self.feature_stds
        X_t = torch.from_numpy(X_norm)
        self.model.train()   # MC dropout: keep dropout ON
        preds = []
        with torch.no_grad():
            for _ in range(self.mc_samples):
                preds.append(self.model(X_t).numpy().flatten())
        preds = np.array(preds)
        return preds.mean(axis=0)

    def predict_with_uncertainty(self, X: pd.DataFrame):
        """Returns (mean, std) over MC samples."""
        import torch
        X_arr = X.fillna(0).values.astype(np.float32)
        X_norm = (X_arr - self.feature_means) / self.feature_stds
        X_t = torch.from_numpy(X_norm)
        self.model.train()
        preds = []
        with torch.no_grad():
            for _ in range(self.mc_samples):
                preds.append(self.model(X_t).numpy().flatten())
        preds = np.array(preds)
        return preds.mean(axis=0), preds.std(axis=0)


# ──────────────────────────────────────────────────────────────────────
# 8. Chronos (Amazon foundation model, zero-shot)
# ──────────────────────────────────────────────────────────────────────


class ChronosForecaster:
    name = "chronos"

    def __init__(self, model_size: str = "small"):
        # 'tiny' | 'mini' | 'small' | 'base' | 'large'
        self.model_size = model_size
        self.pipeline = None

    def available(self) -> bool:
        try:
            from chronos import ChronosPipeline   # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure_loaded(self):
        if self.pipeline is not None:
            return
        from chronos import ChronosPipeline
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.pipeline = ChronosPipeline.from_pretrained(
            f"amazon/chronos-t5-{self.model_size}",
            device_map=device,
            torch_dtype=torch.float32,
        )

    def predict(self, context: np.ndarray, prediction_length: int = 5,
                 num_samples: int = 20) -> np.ndarray:
        """context: 1D historical values; returns (num_samples, prediction_length)."""
        if not self.available():
            raise ImportError("chronos not installed")
        import torch
        self._ensure_loaded()
        ctx_tensor = torch.tensor(context, dtype=torch.float32)
        forecast = self.pipeline.predict(ctx_tensor, prediction_length=prediction_length,
                                           num_samples=num_samples)
        return forecast.numpy().squeeze(0) if hasattr(forecast, "numpy") else forecast


# ──────────────────────────────────────────────────────────────────────
# 9. TimesFM (Google foundation model, zero-shot)
# ──────────────────────────────────────────────────────────────────────


class TimesFMForecaster:
    name = "timesfm"

    def __init__(self, horizon_len: int = 5, context_len: int = 256):
        self.horizon_len = horizon_len
        self.context_len = context_len
        self.tfm = None

    def available(self) -> bool:
        try:
            import timesfm   # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure_loaded(self):
        if self.tfm is not None:
            return
        import timesfm
        self.tfm = timesfm.TimesFm(
            context_len=self.context_len,
            horizon_len=self.horizon_len,
            input_patch_len=32, output_patch_len=128,
            num_layers=20, model_dims=1280,
            backend="cpu",
        )
        self.tfm.load_from_checkpoint(repo_id="google/timesfm-1.0-200m")

    def predict(self, context_series: list[np.ndarray]) -> np.ndarray:
        if not self.available():
            raise ImportError("timesfm not installed")
        self._ensure_loaded()
        forecast, _ = self.tfm.forecast(context_series, freq=[0] * len(context_series))
        return forecast


# ──────────────────────────────────────────────────────────────────────
# 10. FinBERT (HuggingFace)
# ──────────────────────────────────────────────────────────────────────


class FinBERTScorer:
    name = "finbert"

    def __init__(self, model_name: str = "ProsusAI/finbert"):
        self.model_name = model_name
        self.tokenizer = None
        self.model = None

    def available(self) -> bool:
        try:
            import transformers   # noqa: F401
            import torch          # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure_loaded(self):
        if self.model is not None:
            return
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
        self.model.eval()

    def score_batch(self, texts: list[str], batch_size: int = 16) -> list[dict]:
        """Returns per-text {positive, negative, neutral} probabilities."""
        if not self.available():
            raise ImportError("transformers not installed")
        import torch
        self._ensure_loaded()
        out = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            inputs = self.tokenizer(batch, padding=True, truncation=True,
                                      max_length=128, return_tensors="pt")
            with torch.no_grad():
                logits = self.model(**inputs).logits
            probs = torch.softmax(logits, dim=-1).numpy()
            # FinBERT label order: 0=positive, 1=negative, 2=neutral
            for p in probs:
                out.append({"positive": float(p[0]), "negative": float(p[1]),
                            "neutral": float(p[2])})
        return out


# ──────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────


MODEL_REGISTRY = {
    "stacking": StackingEnsemble,
    "gp": GaussianProcessReg,
    "nbeats": NBEATSModel,
    "tft": TFTModel,
    "patchtst": PatchTSTModel,
    "causal_forest": CausalForestModel,
    "bnn_mcdropout": BNNModel,
    "chronos": ChronosForecaster,
    "timesfm": TimesFMForecaster,
    "finbert": FinBERTScorer,
}


def available_models() -> dict[str, bool]:
    """Return availability of each model (dep installed)."""
    return {name: cls().available() for name, cls in MODEL_REGISTRY.items()}
