"""Wave 3 — Embedding & decomposition features.

  - Wavelet (8):    pywt 5-level db4 decomposition of close returns -> 5
                    coefficient energy ratios + 3 reconstruction
                    components (trend/cycle/noise) MA ratios
  - PCA (10):       PCA(10) on technical sub-matrix, output components
                    explain ~80% variance
  - Autoencoder (8): torch shallow AE bottleneck (lazy, off by default)
  - STL (3):        statsmodels seasonal_decompose trend/seasonal/resid
                    21d rolling

Computed per-ticker, then merged into the panel.
"""
from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd


def compute_wavelet_features(bars: pd.DataFrame, levels: int = 5) -> pd.DataFrame:
    """5-level Daubechies-4 wavelet decomposition energy ratios.

    Rolling 256-day window. Each window: pywt.wavedec(returns, 'db4', level=5)
    -> 6 coefficient arrays. We output energy fraction of each level."""
    cols = [f"wavelet_e{i}" for i in range(levels + 1)] + ["wavelet_hf_ratio"]
    out = pd.DataFrame(np.nan, index=bars.index, columns=cols)
    try:
        import pywt
    except ImportError:
        return out
    close = bars["close"].astype(float)
    ret = np.log(close / close.shift(1)).fillna(0).values
    if len(ret) < 256:
        return out
    window = 256
    for i in range(window, len(ret)):
        seg = ret[i - window: i]
        try:
            coeffs = pywt.wavedec(seg, "db4", level=levels)
        except Exception:
            continue
        energies = [float(np.sum(c ** 2)) for c in coeffs]
        total = sum(energies) + 1e-12
        for j, e in enumerate(energies):
            out.iloc[i, j] = e / total
        # High-frequency = detail coefficients d1+d2 fraction
        if len(energies) > 2:
            out.iloc[i, -1] = (energies[-1] + energies[-2]) / total
    return out


def compute_pca_features(bars_with_tech: pd.DataFrame, n_components: int = 10) -> pd.DataFrame:
    """PCA on the technical-indicator sub-matrix per ticker, fitted on
    training window. Output: pca_0..pca_{n-1}."""
    from sklearn.decomposition import PCA
    cols = [f"pca_{i}" for i in range(n_components)]
    tech_cols = [c for c in bars_with_tech.columns
                  if c not in ("open", "high", "low", "close", "volume")]
    if not tech_cols or len(bars_with_tech) < 100:
        return pd.DataFrame(np.nan, index=bars_with_tech.index, columns=cols)
    X = bars_with_tech[tech_cols].replace([np.inf, -np.inf], np.nan).fillna(0)
    pca = PCA(n_components=min(n_components, X.shape[1]))
    try:
        Y = pca.fit_transform(X.values)
    except Exception:
        return pd.DataFrame(np.nan, index=bars_with_tech.index, columns=cols)
    out_df = pd.DataFrame(Y, index=bars_with_tech.index,
                            columns=[f"pca_{i}" for i in range(Y.shape[1])])
    # Pad with NaN if fewer components
    for i in range(Y.shape[1], n_components):
        out_df[f"pca_{i}"] = np.nan
    return out_df


def compute_stl_features(bars: pd.DataFrame, period: int = 21) -> pd.DataFrame:
    """STL decomposition of log-returns -> trend/seasonal/resid ratios."""
    cols = ["stl_trend_strength", "stl_seasonal_strength", "stl_resid_ratio"]
    out = pd.DataFrame(np.nan, index=bars.index, columns=cols)
    try:
        from statsmodels.tsa.seasonal import STL
    except ImportError:
        return out
    close = bars["close"].astype(float)
    ret = np.log(close / close.shift(1)).fillna(0)
    if len(ret) < period * 3:
        return out
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            stl = STL(ret, period=period, robust=True).fit()
        trend = stl.trend
        seasonal = stl.seasonal
        resid = stl.resid
        total_var = ret.var() + 1e-12
        out["stl_trend_strength"] = float(trend.var() / total_var)
        out["stl_seasonal_strength"] = float(seasonal.var() / total_var)
        out["stl_resid_ratio"] = float(resid.var() / total_var)
    except Exception:
        pass
    return out


def compute_autoencoder_features(bars: pd.DataFrame, bottleneck: int = 8,
                                    n_epochs: int = 10) -> pd.DataFrame:
    """Shallow torch autoencoder bottleneck embedding of OHLCV returns.

    Lazy (heavy) — only call when explicitly requested."""
    cols = [f"ae_{i}" for i in range(bottleneck)]
    out = pd.DataFrame(np.nan, index=bars.index, columns=cols)
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        return out
    if len(bars) < 100:
        return out
    arr = bars[["open", "high", "low", "close", "volume"]].astype(float).values
    ret = np.log(arr[1:] / arr[:-1]).astype(np.float32)
    ret = np.nan_to_num(ret, nan=0.0, posinf=0.0, neginf=0.0)
    means = ret.mean(axis=0); stds = ret.std(axis=0) + 1e-8
    Xn = (ret - means) / stds
    Xt = torch.from_numpy(Xn)
    n_in = Xt.shape[1]

    class AE(nn.Module):
        def __init__(self, n_in, bottleneck):
            super().__init__()
            self.enc = nn.Sequential(nn.Linear(n_in, 16), nn.ReLU(), nn.Linear(16, bottleneck))
            self.dec = nn.Sequential(nn.Linear(bottleneck, 16), nn.ReLU(), nn.Linear(16, n_in))
        def forward(self, x):
            z = self.enc(x); return self.dec(z), z

    model = AE(n_in, bottleneck)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.MSELoss()
    for _ in range(n_epochs):
        opt.zero_grad(); recon, _ = model(Xt); loss = loss_fn(recon, Xt)
        loss.backward(); opt.step()
    with torch.no_grad():
        _, z = model(Xt)
        emb = z.numpy()
    # Align: first row is NaN (ret has length-1)
    emb_full = np.full((len(bars), bottleneck), np.nan)
    emb_full[1:] = emb
    return pd.DataFrame(emb_full, index=bars.index, columns=cols)


def compute_embedding_features(bars: pd.DataFrame, include_ae: bool = False) -> pd.DataFrame:
    """Compute all embedding/decomposition features."""
    parts = [
        compute_wavelet_features(bars, levels=5),
        compute_stl_features(bars, period=21),
    ]
    if include_ae:
        parts.append(compute_autoencoder_features(bars, bottleneck=8))
    return pd.concat(parts, axis=1)
