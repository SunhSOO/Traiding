"""Single-shard cache worker — subprocess unit.

Called by cache_feature_matrix_v3.py with tickers + output path.
Builds features for those tickers only, saves parquet, exits.
Memory released by OS on subprocess exit.

Usage (called as subprocess):
    uv run python scripts/cache_shard_worker.py --market US --start 2021-06-04 --end 2026-06-04 --tickers AAPL,MSFT --out var/cache/shards/shard_001.parquet
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import session_scope
from training.features import build_feature_matrix
from training.labels_multi import attach_labels, load_close_panel


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["KR", "US"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--tickers", required=True, help="comma-separated")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with session_scope() as s:
        df, rep = build_feature_matrix(s, market=args.market, start=start, end=end, tickers=tickers)
        if df.empty:
            print(f"[worker] empty for {tickers}"); sys.exit(0)
        close = load_close_panel(s, market=args.market,
                                   start=start - timedelta(days=10), end=end)
        df = attach_labels(df, close)
    df.to_parquet(out_path, index=False)
    print(f"[worker] {len(tickers)} ticker(s): rows={rep.rows_emitted} -> {out_path}")


if __name__ == "__main__":
    main()
