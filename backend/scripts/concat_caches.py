"""Concat 2 feature cache parquets along time axis — Wave 3.

Joins two 5y caches into a single 10y parquet.

Usage:
    uv run python scripts/concat_caches.py \\
        --early var/cache/features_US_2016-06-15_2021-06-13.parquet \\
        --late var/cache/features_US_2021-06-14_2026-06-08.parquet \\
        --out var/cache/features_US_2016-06-15_2026-06-08.parquet
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--early", required=True, help="earlier 5y parquet")
    ap.add_argument("--late", required=True, help="recent 5y parquet")
    ap.add_argument("--out", required=True, help="output 10y parquet")
    args = ap.parse_args()

    e = Path(args.early); l = Path(args.late); o = Path(args.out)
    if not e.exists():
        print(f"[concat] early not found: {e}"); sys.exit(1)
    if not l.exists():
        print(f"[concat] late not found: {l}"); sys.exit(1)

    print(f"[concat] loading {e.name} ({e.stat().st_size/1024/1024:.1f} MB)")
    early = pd.read_parquet(e)
    print(f"  rows={len(early):,} cols={len(early.columns)} dates={early['date'].min()}..{early['date'].max()}")

    print(f"[concat] loading {l.name} ({l.stat().st_size/1024/1024:.1f} MB)")
    late = pd.read_parquet(l)
    print(f"  rows={len(late):,} cols={len(late.columns)} dates={late['date'].min()}..{late['date'].max()}")

    # Deduplicate (in case of overlap)
    combined = pd.concat([early, late], ignore_index=True)
    n_before = len(combined)
    combined = combined.drop_duplicates(subset=["date", "market", "ticker"], keep="last")
    n_after = len(combined)
    if n_before != n_after:
        print(f"  removed {n_before-n_after} duplicates")

    combined = combined.sort_values(["market", "ticker", "date"]).reset_index(drop=True)
    print(f"  combined: rows={len(combined):,} dates={combined['date'].min()}..{combined['date'].max()}")

    o.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(o, index=False)
    print(f"[concat] saved {o} ({o.stat().st_size/1024/1024:.1f} MB)")
    print("\nNote: Rolling features (ret_252d, sma200, vol_252d) recompute optional.")
    print("      Time-boundary rows may have NaN for long-lookback features.")


if __name__ == "__main__":
    main()
