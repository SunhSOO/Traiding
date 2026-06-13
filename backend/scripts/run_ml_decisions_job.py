"""Job: run ML production decisions through the paper broker (both markets).

Mirrors run_jobs.run_decisions' broker/risk setup but drives trades from the
trained bundle via `decision.ml_runner.run_ml_decisions`, into a dedicated
ML paper account ("ml-kr"/"ml-us") so it doesn't mix with the composite
F/T/I account. Regime gating on (bear defense). Uses latest cached features
(a live job would rebuild features for today).

Usage:
    uv run python scripts/run_ml_decisions_job.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import session_scope
from core.risk import RiskEngine, RiskLimits
from core.types import Market
from brokers.db_price_oracle import DBPriceOracle
from brokers.paper import PaperBroker
from brokers.paper_persistence import load_or_create_account, rehydrate_logic
from decision.ml_runner import run_ml_decisions
from decision.production_inference import (
    ProductionRecommender, latest_rows_from_cache, latest_close_map,
)


def main() -> None:
    now = datetime.now(timezone.utc)
    risk_engine, risk_limits = RiskEngine(), RiskLimits()
    oracle = DBPriceOracle(session_factory=session_scope)
    accounts = [
        (Market.KR, "ml-kr", "KRW", 1_000_000.0),
        (Market.US, "ml-us", "USD", 1_000.0),
    ]
    for market, acct_name, ccy, init_bal in accounts:
        cache = f"var/_fs_ab_{market.value}_365.parquet"
        if not Path(cache).exists():
            print(f"[{market.value}] no cache; skip"); continue
        rec = ProductionRecommender(market.value)
        feat = latest_rows_from_cache(cache)
        with session_scope() as s:
            close_map = latest_close_map(s, market.value)
            account = load_or_create_account(s, name=acct_name,
                                             base_currency=ccy, initial_balance=init_bal)
            logic = rehydrate_logic(s, account)
            account_id = account.id
        broker = PaperBroker(logic, oracle, session_factory=session_scope, account_id=account_id)
        with session_scope() as s:
            rep = run_ml_decisions(
                s, market=market, as_of=now, broker=broker,
                risk_engine=risk_engine, risk_limits=risk_limits,
                recommender=rec, feat_df=feat, close_map=close_map,
                n_long=20, regime_gate=True,
            )
        acct = broker.get_account()
        pos = broker.get_positions()
        pos_val = sum((p.current_price or p.entry_price) * p.volume for p in pos)
        total = float(acct.balance) + pos_val
        print(f"[{market.value}] regime={rep.regime} defensive={rep.defensive}  "
              f"BUY={rep.buys_executed} SELL={rep.sells_executed} rejected={rep.rejected}  "
              f"positions={len(pos)} cash={acct.balance:,.0f} +pos={pos_val:,.0f} "
              f"= 총 {total:,.0f}{ccy}  errors={len(rep.errors)}", flush=True)
        if rep.errors:
            print("   ", rep.errors[:3])


if __name__ == "__main__":
    main()
