"""FDR-based KR universe + daily-price backfill.

FDR does not expose KOSPI200 / KOSDAQ150 constituents directly, so we
use a market-cap proxy:

    * KOSPI200 = top-200 KOSPI tickers by Marcap
    * KOSDAQ150 = top-150 KOSDAQ tickers by Marcap

This is NOT survivorship-bias-free history. The proper fix is to wire
the KRX CSV download (or pykrx when its endpoint recovers) into
`data/universe/kr_universe.py`. For now this script unblocks Phase 1
ingestion for the Korean market end-to-end.

Usage:

    uv run python scripts/fdr_backfill_kr.py universe
    uv run python scripts/fdr_backfill_kr.py prices [--start ...] [--end ...]
    uv run python scripts/fdr_backfill_kr.py all
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import FinanceDataReader as fdr
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.db import session_scope
from core.models.prices import DailyPrice, UniverseMembership
from core.models.universe import Security


def _kr_index_pick() -> tuple[list[dict], dict[str, set[str]]]:
    """Returns (security_rows, ticker -> {index_codes}).

    Combines KRX-MARCAP (for ranking + Code/Name/Market) with KRX-DESC
    (for sector/industry/listing_date). Top-200 KOSPI -> KOSPI200,
    top-150 KOSDAQ -> KOSDAQ150.
    """
    marcap = fdr.StockListing("KRX-MARCAP")
    desc = fdr.StockListing("KRX-DESC")

    desc_by_code = {str(r["Code"]).zfill(6): r for _, r in desc.iterrows()}

    kospi = marcap[marcap["Market"] == "KOSPI"].sort_values("Marcap", ascending=False)
    kosdaq = marcap[marcap["Market"] == "KOSDAQ"].sort_values("Marcap", ascending=False)

    membership: dict[str, set[str]] = {}
    pick_kospi200 = kospi.head(200)
    pick_kosdaq150 = kosdaq.head(150)
    for _, row in pick_kospi200.iterrows():
        t = str(row["Code"]).zfill(6)
        membership.setdefault(t, set()).add("KOSPI200")
    for _, row in pick_kosdaq150.iterrows():
        t = str(row["Code"]).zfill(6)
        membership.setdefault(t, set()).add("KOSDAQ150")

    rows: list[dict] = []
    seen: set[str] = set()
    for src in (pick_kospi200, pick_kosdaq150):
        for _, row in src.iterrows():
            code = str(row["Code"]).zfill(6)
            if code in seen:
                continue
            seen.add(code)
            d = desc_by_code.get(code, {})
            listed = None
            ld = d.get("ListingDate") if isinstance(d, dict) or hasattr(d, "get") else None
            try:
                if ld is not None and str(ld) not in ("", "nan", "None"):
                    listed = datetime.strptime(str(ld)[:10], "%Y-%m-%d").date()
            except Exception:
                listed = None
            rows.append({
                "market": "KR",
                "ticker": code,
                "name": str(row["Name"]),
                "name_en": None,
                "isin": str(row.get("ISU_CD", "") or "")[:12] or None,
                "cik": None,
                "corp_code": None,
                "exchange": str(row["Market"]),
                "index_membership": ",".join(sorted(membership[code])),
                "sector": (d.get("Sector") if hasattr(d, "get") else None) or None,
                "industry": (d.get("Industry") if hasattr(d, "get") else None) or None,
                "listed_date": listed,
                "delisted_date": None,
                "is_active": True,
                "currency": "KRW",
            })
    return rows, membership


def cmd_universe() -> None:
    rows, membership = _kr_index_pick()
    print(f"KR universe :: kospi200_proxy + kosdaq150_proxy = {len(rows)} rows")
    now = datetime.now(timezone.utc)
    today = date.today()

    with session_scope() as s:
        stmt = pg_insert(Security).values(rows)
        update_cols = {
            c.name: c for c in stmt.excluded
            if c.name not in ("market", "ticker", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            constraint="pk_securities", set_=update_cols,
        )
        s.execute(stmt)
        s.flush()

        # Open new memberships, close vanished ones (idempotent).
        fetched: set[tuple[str, str]] = set()
        for t, idxs in membership.items():
            for ix in idxs:
                fetched.add((t, ix))

        open_rows = list(s.scalars(
            select(UniverseMembership).where(
                UniverseMembership.market == "KR",
                UniverseMembership.valid_to.is_(None),
            )
        ))
        open_keys = {(r.ticker, r.index_code) for r in open_rows}

        new_keys = fetched - open_keys
        for ticker, index_code in new_keys:
            s.add(UniverseMembership(
                market="KR", ticker=ticker, index_code=index_code,
                valid_from=today, valid_to=None,
                source="fdr_marcap_proxy", as_of_ts=now,
            ))
        closed = 0
        for row in open_rows:
            if (row.ticker, row.index_code) not in fetched:
                row.valid_to = today
                closed += 1
        print(f"  memberships: opened={len(new_keys)} closed={closed}")


def cmd_prices(start: str, end: str, batch: int, skip_existing_min_rows: int,
               limit: int) -> None:
    print(f"KR price backfill :: {start} -> {end}")
    with session_scope() as s:
        tickers = list(s.scalars(
            select(Security.ticker)
            .where(Security.market == "KR", Security.is_active.is_(True))
            .order_by(Security.ticker)
        ))
    print(f"  {len(tickers)} active KR tickers in DB")

    if skip_existing_min_rows > 0:
        with session_scope() as s:
            already = dict(s.execute(
                select(DailyPrice.ticker, func.count())
                .where(DailyPrice.market == "KR")
                .group_by(DailyPrice.ticker)
            ).all())
        before = len(tickers)
        tickers = [t for t in tickers if already.get(t, 0) < skip_existing_min_rows]
        print(f"  skipping {before - len(tickers)} tickers already at >= "
              f"{skip_existing_min_rows} rows")

    if limit > 0:
        tickers = tickers[:limit]
        print(f"  limited to first {len(tickers)} tickers")

    total_ok, total_fail, total_rows = 0, 0, 0
    batch_rows: list[dict] = []
    fail_examples: list[str] = []
    as_of = datetime.now(timezone.utc)

    for i, t in enumerate(tickers, start=1):
        try:
            df = fdr.DataReader(t, start=start, end=end)
        except Exception as e:
            total_fail += 1
            if len(fail_examples) < 5:
                fail_examples.append(f"{t}: {type(e).__name__}")
            continue
        if df is None or df.empty:
            total_fail += 1
            if len(fail_examples) < 5:
                fail_examples.append(f"{t}: empty")
            continue
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
            batch_rows.append({
                "market": "KR", "ticker": t,
                "trade_date": idx.date() if hasattr(idx, "date") else idx,
                "open": float(o), "high": float(h), "low": float(lo),
                "close": float(close), "volume": int(v), "adj_close": adj,
                "source": "fdr", "as_of_ts": as_of,
            })
        total_ok += 1

        if i % batch == 0 or i == len(tickers):
            if batch_rows:
                CHUNK = 2000
                with session_scope() as s:
                    for k in range(0, len(batch_rows), CHUNK):
                        chunk = batch_rows[k:k + CHUNK]
                        stmt = pg_insert(DailyPrice).values(chunk).on_conflict_do_nothing(
                            index_elements=["trade_date", "market", "ticker"]
                        )
                        s.execute(stmt)
                total_rows += len(batch_rows)
                batch_rows.clear()
            print(f"  [{i:4d}/{len(tickers)}] ok={total_ok} fail={total_fail} "
                  f"rows-attempted={total_rows}", flush=True)

    print(f"\nDone. ok={total_ok} fail={total_fail} rows-attempted={total_rows}")
    if fail_examples:
        print("  first failures:", ", ".join(fail_examples))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["universe", "prices", "all"])
    ap.add_argument("--start", default=(date.today() - timedelta(days=540)).isoformat())
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-existing-min-rows", type=int, default=200)
    args = ap.parse_args()

    if args.cmd in ("universe", "all"):
        cmd_universe()
    if args.cmd in ("prices", "all"):
        cmd_prices(args.start, args.end, args.batch,
                   args.skip_existing_min_rows, args.limit)


if __name__ == "__main__":
    main()
