"""Job: run the INTEGRATED selection→execution pipeline (both markets).

A. market read + B. basket selection (alpha) → C. per-stock technical timing →
D. risk + paper broker + 2-stage audit, into dedicated `core-kr`/`core-us`
accounts (D2). Regime/exposure overlay built in (defensive when exposure≈0).
Uses latest cached features (a live job would rebuild features for today).

Usage:
    uv run python scripts/run_integrated_job.py
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
from decision.integrated_runner import run_integrated_decisions
from decision.production_inference import (
    ProductionRecommender, latest_rows_from_cache, latest_close_map,
)

ACCOUNTS = [
    (Market.KR, "core-kr", "KRW", 1_000_000.0),
    (Market.US, "core-us", "USD", 1_000.0),
]


def main() -> None:
    now = datetime.now(timezone.utc)
    risk_engine, risk_limits = RiskEngine(), RiskLimits()
    oracle = DBPriceOracle(session_factory=session_scope)
    for market, acct_name, ccy, init_bal in ACCOUNTS:
        cache = f"var/_fs_ab_{market.value}_365.parquet"
        if not Path(cache).exists():
            print(f"[{market.value}] no cache; skip"); continue
        rec = ProductionRecommender(market.value)
        feat = latest_rows_from_cache(cache)
        with session_scope() as s:
            close_map = latest_close_map(s, market.value)
            account = load_or_create_account(s, name=acct_name, base_currency=ccy,
                                             initial_balance=init_bal)
            logic = rehydrate_logic(s, account)
            account_id = account.id
        broker = PaperBroker(logic, oracle, session_factory=session_scope, account_id=account_id)
        with session_scope() as s:
            rep = run_integrated_decisions(
                s, market=market, as_of=now, broker=broker,
                risk_engine=risk_engine, risk_limits=risk_limits,
                recommender=rec, feat_df=feat, close_map=close_map,
            )
        acct = broker.get_account()
        pos = broker.get_positions()
        pos_val = sum((p.current_price or p.entry_price) * p.volume for p in pos)
        total = float(acct.balance) + pos_val
        print(f"[{market.value}] regime={rep.regime} exposure={rep.target_exposure} "
              f"defensive={rep.defensive} breadth={rep.breadth} basket={rep.basket_size}  "
              f"BUY={rep.buys_executed} SELL={rep.sells_executed} WAIT={rep.waiting} "
              f"rej={rep.rejected}  pos={len(pos)} cash={acct.balance:,.0f} +pos={pos_val:,.0f} "
              f"= {total:,.0f}{ccy} errors={len(rep.errors)}", flush=True)
        if rep.errors:
            print("   ", rep.errors[:3])


if __name__ == "__main__":
    main()
