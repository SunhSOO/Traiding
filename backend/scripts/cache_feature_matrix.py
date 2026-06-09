"""Build feature matrix once and cache to parquet — Wave 3.

Reuse across Optuna / v3 trainer / ensemble optimization to avoid the
expensive 1-2 hour matrix build per script.

Outputs:
  var/cache/features_{market}_{Yfrom}_{Yto}.parquet
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import session_scope
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel
from training.features_cross_section import apply_cross_section_features

from sqlalchemy import select
from core.models.training import TickerClusterAssignment as TickerCluster


def attach_clusters_inline(df, session):
    import pandas as pd
    rows = list(session.execute(
        select(TickerCluster.market, TickerCluster.ticker, TickerCluster.cluster_id)
    ).all())
    if not rows:
        df["cluster_id"] = "__none__"; return df
    cluster_df = pd.DataFrame(rows, columns=["market", "ticker", "cluster_id"])
    return df.merge(cluster_df, on=["market", "ticker"], how="left")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--days", type=int, default=1820, help="5y default")
    ap.add_argument("--out-dir", default="var/cache")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    fname = out_dir / f"features_{args.market}_{start}_{end}.parquet"

    if fname.exists():
        print(f"[cache] exists: {fname}")
        return

    print(f"[cache] building {args.market} {start} -> {end}")
    t0 = time.time()
    with session_scope() as s:
        df, rep = build_feature_matrix(s, market=args.market, start=start, end=end)
        if df.empty:
            print("[cache] empty"); sys.exit(1)
        print(f"  features built: rows={rep.rows_emitted} cols={rep.feature_columns} "
              f"tickers={rep.tickers_processed} ({time.time()-t0:.0f}s)")
        close = load_close_panel(s, market=args.market,
                                   start=start - timedelta(days=10), end=end)
        df = attach_labels(df, close)
        df = attach_clusters_inline(df, s)
    print(f"  attached labels + clusters ({time.time()-t0:.0f}s)")

    df = apply_cross_section_features(df)
    print(f"  applied cross-section ({time.time()-t0:.0f}s)")

    df.to_parquet(fname, index=False)
    size_mb = fname.stat().st_size / 1024 / 1024
    print(f"[cache] saved {fname} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
