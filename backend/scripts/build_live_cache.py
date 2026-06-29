"""Build the LIVE feature cache (recent window) with the CURRENT pipeline.

The integrated/ML jobs read `var/_fs_ab_{market}_{days}.parquet` for live
inference (latest row per ticker). The current pipeline includes Blitz
residual-momentum etc., so a stale cache would miss columns the bundle needs.
This rebuilds the prepared matrix (features + labels + cross-section) for the
recent `--days` window. Cache-only — no A/B analysis.

Usage: uv run python scripts/build_live_cache.py --market US --days 365
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import session_scope
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel
from training.features_cross_section import apply_cross_section_features


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--days", type=int, default=365)
    args = ap.parse_args()

    cache = Path(f"var/_fs_ab_{args.market}_{args.days}.parquet")
    end = date.today()
    start = end - timedelta(days=args.days)
    print(f"[live-cache] building {args.market} {start}..{end} (current pipeline)", flush=True)
    with session_scope() as s:
        feat_df, rep = build_feature_matrix(s, market=args.market, start=start, end=end)
    with session_scope() as s:
        close_panel = load_close_panel(s, market=args.market,
                                       start=start - timedelta(days=10), end=end)
    feat_df = attach_labels(feat_df, close_panel)
    feat_df = apply_cross_section_features(feat_df)
    blitz = [c for c in feat_df.columns if "blitz" in c]
    feat_df.to_parquet(cache, index=False)
    print(f"[live-cache] {args.market} rows={len(feat_df)} cols={feat_df.shape[1]} "
          f"blitz={blitz} -> {cache}", flush=True)


if __name__ == "__main__":
    main()
