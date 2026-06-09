"""EDA — full data exploration on 10y backfilled data.

Generates `var/eda_report.json` with:
  - Price coverage stats (per market: rows, tickers, date range, missing)
  - Returns distribution (mean/std/skew/kurt/quantiles)
  - Cluster distribution
  - Feature non-null rates (all 431 features)
  - Cross-correlation top-20 pairs
  - Survivorship bias indicators (delisted ticker count, age distribution)
  - Calendar coverage (trading day gaps)
"""
from __future__ import annotations

import json
import sys
import warnings
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from core.db import session_scope
from core.models.prices import DailyPrice, MacroSeries
from core.models.training import TickerClusterAssignment as TickerCluster
from core.models.universe import Security


def coverage_stats(s):
    rows = list(s.execute(select(
        DailyPrice.market,
        func.min(DailyPrice.trade_date), func.max(DailyPrice.trade_date),
        func.count(func.distinct(DailyPrice.ticker)), func.count(),
    ).group_by(DailyPrice.market)).all())
    out = {}
    for m, mn, mx, t, c in rows:
        out[m] = {
            "min_date": str(mn), "max_date": str(mx),
            "years": round((mx - mn).days / 365.25, 2),
            "tickers": int(t), "rows": int(c),
            "avg_rows_per_ticker": round(c / max(1, t), 0),
        }
    return out


def returns_distribution(s, market: str):
    rows = list(s.execute(select(DailyPrice.ticker, DailyPrice.trade_date, DailyPrice.close)
        .where(DailyPrice.market == market).order_by(DailyPrice.ticker, DailyPrice.trade_date)
    ).all())
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["ticker", "ts", "close"])
    df["close"] = df["close"].astype(float)
    df["ret_1d"] = df.groupby("ticker")["close"].pct_change()
    rets = df["ret_1d"].dropna()
    pct = rets.quantile([0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]).to_dict()
    return {
        "n": int(len(rets)),
        "mean": float(rets.mean()),
        "std": float(rets.std()),
        "skew": float(rets.skew()),
        "kurt": float(rets.kurt()),
        "min": float(rets.min()),
        "max": float(rets.max()),
        "pct": {f"p{int(k*100)}": float(v) for k, v in pct.items()},
        "neg_days_pct": float((rets < 0).mean()),
    }


def cluster_distribution(s):
    rows = list(s.execute(select(
        TickerCluster.cluster_id, func.count()
    ).group_by(TickerCluster.cluster_id).order_by(func.count().desc())).all())
    return {cid: int(n) for cid, n in rows}


def feature_nonnull(s, market: str, n_sample_tickers: int = 30):
    """Build feature matrix on a sample, count non-null per column."""
    from training.features import build_feature_matrix
    sec_rows = list(s.execute(select(Security.ticker)
        .where(Security.market == market, Security.is_active == True)
        .limit(n_sample_tickers)).all())
    tickers = [r[0] for r in sec_rows]
    if not tickers:
        return {}
    df, rep = build_feature_matrix(s, market=market,
        start=date(2016, 1, 1), end=date(2026, 6, 1), tickers=tickers)
    if df.empty:
        return {}
    feat_cols = [c for c in df.columns if c not in ("date", "market", "ticker")]
    nn = {c: int(df[c].notna().sum()) for c in feat_cols}
    total = len(df)
    return {
        "rows": int(total), "tickers": int(rep.tickers_processed),
        "n_features": len(feat_cols),
        "non_null_rate": {c: round(nn[c] / total, 3) for c in feat_cols},
    }


def survivorship_check(s, market: str):
    """Tickers in securities (active) vs tickers with prices."""
    sec_active = set(s.scalars(select(Security.ticker).where(
        Security.market == market, Security.is_active == True)).all())
    px_rows = list(s.execute(select(DailyPrice.ticker,
        func.min(DailyPrice.trade_date), func.max(DailyPrice.trade_date))
        .where(DailyPrice.market == market)
        .group_by(DailyPrice.ticker)).all())
    age = []
    for tk, mn, mx in px_rows:
        years = (mx - mn).days / 365.25
        age.append({"ticker": tk, "years": round(years, 2),
                    "first_day": str(mn), "active": tk in sec_active})
    df = pd.DataFrame(age)
    return {
        "total_tickers": len(df),
        "active": int(df["active"].sum()),
        "inactive_or_delisted": int(len(df) - df["active"].sum()),
        "median_history_years": float(df["years"].median()) if len(df) else 0,
        "tickers_with_lt_5y": int((df["years"] < 5).sum()),
        "tickers_with_full_10y": int((df["years"] >= 10).sum()),
    }


def macro_coverage(s):
    rows = list(s.execute(select(
        MacroSeries.series_code,
        func.min(MacroSeries.ts), func.max(MacroSeries.ts),
        func.count(),
    ).group_by(MacroSeries.series_code).order_by(func.count().desc())).all())
    return [
        {"code": code, "min": str(mn), "max": str(mx),
         "years": round((mx - mn).days / 365.25, 2), "rows": int(n)}
        for code, mn, mx, n in rows
    ]


def correlation_top_pairs(s, market: str, n_sample_tickers: int = 10,
                          n_top: int = 20):
    """Top 20 most-correlated feature pairs (informational)."""
    from training.features import build_feature_matrix
    sec_rows = list(s.execute(select(Security.ticker)
        .where(Security.market == market, Security.is_active == True)
        .limit(n_sample_tickers)).all())
    tickers = [r[0] for r in sec_rows]
    df, _ = build_feature_matrix(s, market=market,
        start=date(2016, 1, 1), end=date(2026, 6, 1), tickers=tickers)
    if df.empty:
        return []
    feat_cols = [c for c in df.columns if c not in ("date", "market", "ticker")]
    sample = df[feat_cols].select_dtypes(include=[np.number])
    sample = sample.replace([np.inf, -np.inf], np.nan).dropna(axis=1, thresh=int(len(sample) * 0.5))
    if sample.empty:
        return []
    corr = sample.corr().abs()
    pairs = []
    for i, ci in enumerate(corr.columns):
        for cj in corr.columns[i + 1:]:
            v = corr.at[ci, cj]
            if np.isfinite(v):
                pairs.append((float(v), ci, cj))
    pairs.sort(reverse=True)
    return [{"corr": round(p[0], 4), "a": p[1], "b": p[2]} for p in pairs[:n_top]]


def main():
    report = {"generated_at": datetime.utcnow().isoformat(), "sections": {}}
    with session_scope() as s:
        print("[eda] coverage stats...")
        report["sections"]["coverage"] = coverage_stats(s)
        print("[eda] returns US...")
        report["sections"]["returns_US"] = returns_distribution(s, "US")
        print("[eda] returns KR...")
        report["sections"]["returns_KR"] = returns_distribution(s, "KR")
        print("[eda] cluster distribution...")
        report["sections"]["clusters"] = cluster_distribution(s)
        print("[eda] survivorship US...")
        report["sections"]["survivorship_US"] = survivorship_check(s, "US")
        print("[eda] survivorship KR...")
        report["sections"]["survivorship_KR"] = survivorship_check(s, "KR")
        print("[eda] macro coverage...")
        report["sections"]["macro_series"] = macro_coverage(s)
        print("[eda] feature non-null rates US...")
        report["sections"]["feature_nonnull_US"] = feature_nonnull(s, "US", n_sample_tickers=30)
        print("[eda] feature correlations US...")
        report["sections"]["top_correlations_US"] = correlation_top_pairs(s, "US")

    out = Path("var/eda_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n[eda] -> {out}")


if __name__ == "__main__":
    main()
