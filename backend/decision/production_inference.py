"""Production inference — turn a trained bundle into trading recommendations.

Loads `var/models/production_{market}.joblib` (built by
scripts/train_production.py) and, for the latest feature rows, emits per
ticker exactly what the auto-trader needs:

  * rank_score / rank_pct  — cross-sectional standing today (selection)
  * pred_ret (q50)         — expected 21d return
  * target_price           — last_close * (1 + q50)
  * band_low / band_high   — conformal-calibrated 80% price band
  * action                 — BUY (top decile) / SELL (bottom decile) / HOLD
  * size_fraction          — base * rank-confidence * regime, capped

The model only carries a weak-but-real cross-sectional edge (walk-forward
rank IC ~0.2-0.28), so this is a *diversified tilt*, not a single-name
oracle: size scales with rank confidence and is capped, and the price band
is deliberately wide and calibrated.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

_BUNDLE_DIR = Path(__file__).resolve().parents[1] / "var" / "models"


@dataclass
class SizingConfig:
    base_fraction: float = 0.05
    max_fraction: float = 0.20
    buy_pct: float = 0.90     # top decile -> BUY
    sell_pct: float = 0.10    # bottom decile -> SELL/AVOID


class ProductionRecommender:
    def __init__(self, market: str, bundle_dir: Optional[Path] = None):
        path = (bundle_dir or _BUNDLE_DIR) / f"production_{market}.joblib"
        self.bundle = joblib.load(path)
        self.market = market
        self.feature_cols = self.bundle["feature_cols"]
        self.alpha = self.bundle.get("alpha", 0.2)
        self.Q = self.bundle.get("conformal_Q", 0.0)

    def recommend(
        self,
        feat_df: pd.DataFrame,
        close_map: dict[str, float],
        *,
        regime_scaler: float = 1.0,
        cfg: SizingConfig = SizingConfig(),
    ) -> pd.DataFrame:
        """feat_df: one latest row per ticker (must contain 'ticker' + features)."""
        X = feat_df[self.feature_cols].astype(float)
        q = self.bundle["quantile_models"]
        lo_q, hi_q = self.alpha / 2, 1 - self.alpha / 2
        pred_mid = q[0.5].predict(X)
        ret_lo = q[lo_q].predict(X) - self.Q   # conformal-widened
        ret_hi = q[hi_q].predict(X) + self.Q

        out = pd.DataFrame({
            "ticker": feat_df["ticker"].values,
            "rank_score": self.bundle["rank_model"].predict(X),
            "pred_ret": pred_mid, "ret_lo": ret_lo, "ret_hi": ret_hi,
        })
        out["rank_pct"] = out["rank_score"].rank(pct=True)
        out["last_close"] = out["ticker"].map(close_map)
        out["target_price"] = out["last_close"] * (1 + out["pred_ret"])
        out["band_low"] = out["last_close"] * (1 + out["ret_lo"])
        out["band_high"] = out["last_close"] * (1 + out["ret_hi"])

        # Action requires the rank model (relative) and the return model
        # (absolute q50) to AGREE — the two are separate models and can
        # contradict (bottom-rank but positive expected return). Demanding
        # agreement removes incoherent calls and is the conservative choice
        # (esp. for KR where shorting is restricted -> BUY needs q50>0).
        out["action"] = "HOLD"
        out.loc[(out["rank_pct"] >= cfg.buy_pct) & (out["pred_ret"] > 0), "action"] = "BUY"
        out.loc[(out["rank_pct"] <= cfg.sell_pct) & (out["pred_ret"] < 0), "action"] = "SELL"
        # Size: confidence = how far from the median rank (0..1), scaled by regime.
        conf = (out["rank_pct"] - 0.5).abs() * 2.0
        out["size_fraction"] = np.minimum(
            cfg.base_fraction * (1.0 + conf) * regime_scaler, cfg.max_fraction
        )
        out.loc[out["action"] == "HOLD", "size_fraction"] = 0.0
        return out.sort_values("rank_score", ascending=False).reset_index(drop=True)


# ── helpers for offline use (latest rows from the feature cache) ──────────

def latest_rows_from_cache(cache_path: str | Path) -> pd.DataFrame:
    """Take the most recent feature row per ticker from a cached matrix."""
    df = pd.read_parquet(cache_path)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").groupby("ticker", as_index=False).tail(1)


def latest_close_map(session, market: str) -> dict[str, float]:
    from sqlalchemy import text
    rows = session.execute(text(
        "SELECT DISTINCT ON (ticker) ticker, close FROM daily_prices "
        "WHERE market = :m ORDER BY ticker, trade_date DESC"
    ), {"m": market}).all()
    return {t: float(c) for t, c in rows}


if __name__ == "__main__":
    # Smoke: print live-style recommendations for both markets from the cache.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.db import session_scope

    for market in ("US", "KR"):
        cache = f"var/_fs_ab_{market}_365.parquet"
        if not Path(cache).exists():
            print(f"[{market}] no cache {cache}; skip"); continue
        rec = ProductionRecommender(market)
        feat = latest_rows_from_cache(cache)
        with session_scope() as s:
            close_map = latest_close_map(s, market)
        df = rec.recommend(feat, close_map)
        m = rec.bundle["metrics"]
        print(f"\n===== {market} recommendations "
              f"(walk-fwd rank IC {m.get('rank_ic_walkfwd_mean'):+.3f}, "
              f"band {int((1-rec.alpha)*100)}%) =====")
        buys = df[df.action == "BUY"].head(8)
        cols = ["ticker", "rank_pct", "pred_ret", "last_close", "target_price",
                "band_low", "band_high", "size_fraction"]
        with pd.option_context("display.float_format", lambda v: f"{v:,.3f}"):
            print("  TOP BUYs:")
            print(buys[cols].to_string(index=False))
            print("  BOTTOM (SELL/AVOID):")
            print(df[df.action == "SELL"].tail(4)[cols].to_string(index=False))
