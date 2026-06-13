"""Bridge: production-model recommendations -> system decision_audit rows.

Runs `ProductionRecommender` for each market and persists the non-HOLD calls
into `decision_audit` (same table the composite F/T/I runner writes), so the
ML model's picks show up in the system's audit trail / UI alongside a target
price and a conformal band. This is additive and safe — it records decisions;
it does NOT yet route them through gates/risk/paper-broker (that wiring, with
the live feature build, is the next step).

Uses the latest feature rows from the cached matrix (offline). A live job
would rebuild features for today instead.

Usage:
    uv run python scripts/run_ml_recommendations.py [--persist]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import session_scope
from core.models.audit import DecisionAudit
from decision.production_inference import (
    ProductionRecommender, latest_rows_from_cache, latest_close_map,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--persist", action="store_true", help="write rows to decision_audit")
    ap.add_argument("--days", type=int, default=365)
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    for market in ("US", "KR"):
        cache = f"var/_fs_ab_{market}_{args.days}.parquet"
        if not Path(cache).exists():
            print(f"[{market}] no cache {cache}; skip"); continue
        rec = ProductionRecommender(market)
        feat = latest_rows_from_cache(cache)
        with session_scope() as s:
            close_map = latest_close_map(s, market)
        df = rec.recommend(feat, close_map)
        acted = df[df["action"] != "HOLD"]
        currency = "KRW" if market == "KR" else "USD"
        print(f"[{market}] {len(acted)} actionable (BUY {int((acted.action=='BUY').sum())} / "
              f"SELL {int((acted.action=='SELL').sum())}) of {len(df)}", flush=True)

        if not args.persist:
            continue
        rows = []
        for _, r in acted.iterrows():
            rows.append(DecisionAudit(
                market=market, ticker=str(r["ticker"]), decision_ts=now,
                composite_score=float(round((r["rank_pct"] - 0.5) * 200, 3)),  # -100..100
                composite_confidence=float(round(abs(r["rank_pct"] - 0.5) * 2, 4)),
                action=str(r["action"]),
                size_value=float(round(r["size_fraction"], 6)), size_currency=currency,
                model_version=f"production_{market}_v1",
                inputs_snapshot={
                    "rank_score": float(r["rank_score"]), "rank_pct": float(r["rank_pct"]),
                    "pred_ret_21d": float(r["pred_ret"]),
                    "last_close": float(r["last_close"]),
                    "target_price": float(r["target_price"]),
                    "band_low": float(r["band_low"]), "band_high": float(r["band_high"]),
                    "source": "production_bundle",
                },
            ))
        with session_scope() as s:
            s.add_all(rows)
        print(f"[{market}] persisted {len(rows)} decision_audit rows.", flush=True)


if __name__ == "__main__":
    main()
