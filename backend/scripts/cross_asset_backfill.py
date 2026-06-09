"""Backfill cross-asset proxies (sector ETFs + commodity ETFs).

These don't belong to the SP500/KOSPI universes; they go into
``daily_prices`` with market='MACRO' (or 'US' for the actual US-listed
ETFs that happen to be in our universe already) so the LightGBM
trainer can pull them via the same SQL as any ticker.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import FinanceDataReader as fdr
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.db import session_scope
from core.models.prices import DailyPrice
from core.models.universe import Security


# Symbol -> (market_label, FDR_symbol)
TARGETS: list[tuple[str, str]] = [
    ("XLK", "US"), ("XLF", "US"), ("XLV", "US"), ("XLE", "US"),
    ("XLY", "US"), ("XLP", "US"), ("XLI", "US"), ("XLB", "US"),
    ("XLU", "US"), ("XLRE", "US"), ("XLC", "US"),
    ("SPY", "US"), ("QQQ", "US"), ("IWM", "US"),
    ("GLD", "MACRO"), ("USO", "MACRO"), ("TLT", "MACRO"),
]


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=(date.today() - timedelta(days=540)).isoformat())
    ap.add_argument("--end", default=date.today().isoformat())
    args = ap.parse_args()
    start = args.start
    end = args.end
    now = datetime.now(timezone.utc)
    print(f"Cross-asset backfill :: {start} -> {end}")
    summary: list[tuple[str, int, str]] = []

    for sym, mkt in TARGETS:
        # Ensure ticker exists in securities (lightweight stub for non-SP500 ETFs)
        with session_scope() as s:
            existing = s.get(Security, (mkt, sym))
            if existing is None:
                s.add(Security(
                    market=mkt, ticker=sym, name=sym,
                    exchange="ETF", index_membership=None,
                    sector="ETF", industry="ETF",
                    is_active=True, currency="USD",
                ))
                s.flush()
        try:
            df = fdr.DataReader(sym, start=start, end=end)
        except Exception as e:
            print(f"  {sym} ({mkt}): FAIL {type(e).__name__}: {e}")
            summary.append((sym, 0, "FAIL"))
            continue
        if df is None or df.empty:
            summary.append((sym, 0, "empty"))
            continue

        rows = []
        for idx, row in df.iterrows():
            close = row["Close"]
            if close != close:
                continue
            o = row["Open"] if row["Open"] == row["Open"] else close
            h = row["High"] if row["High"] == row["High"] else close
            lo = row["Low"] if row["Low"] == row["Low"] else close
            v = row["Volume"] if row["Volume"] == row["Volume"] else 0
            ac = row.get("Adj Close", close)
            adj = float(ac) if ac == ac else float(close)
            d = idx.date() if hasattr(idx, "date") else idx
            rows.append({
                "market": mkt, "ticker": sym,
                "trade_date": d,
                "open": float(o), "high": float(h), "low": float(lo),
                "close": float(close), "volume": int(v), "adj_close": adj,
                "source": "fdr",
                "as_of_ts": datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).replace(hour=16),
            })
        if not rows:
            summary.append((sym, 0, "no close"))
            continue

        CHUNK = 2000
        with session_scope() as s:
            for k in range(0, len(rows), CHUNK):
                stmt = pg_insert(DailyPrice).values(rows[k:k + CHUNK]).on_conflict_do_nothing(
                    index_elements=["trade_date", "market", "ticker"]
                )
                s.execute(stmt)
        print(f"  {sym} ({mkt}): {len(rows)} rows")
        summary.append((sym, len(rows), "ok"))

    print("\nSummary:")
    for sym, n, status in summary:
        print(f"  {sym:8s} rows={n:>5d}  {status}")


if __name__ == "__main__":
    main()
