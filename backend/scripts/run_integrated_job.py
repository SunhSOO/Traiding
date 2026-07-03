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

# Direction-gated concentrate — per market. US is backtest-validated (Sharpe
# 0.62→0.72, direction-gated); KR is NOT (gating fails even pooled). Kept OFF for
# both until (a) a longer direction-history cache exists for live (the 365d cache
# is thin vs the validated long history) and (b) paper confirms the base system.
# Flip US→True to activate the validated lever. The direction score is computed &
# logged regardless for observability.
CONCENTRATE_BY_MARKET = {"US": False, "KR": False}

# Stop-loss (risk management, ON by default): backtest — a 10-15% stop halves the
# worst-window loss (US -14.8%→-8.5%, KR -31%→-9.7%) AND lifts Sharpe (cut losers,
# let winners run). 0.15 chosen over the backtest-best 0.10 to reduce whipsaw/gap
# risk that the clean daily-close backtest understates.
STOP_LOSS = 0.15


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
        # market-direction score (full-history frame) — observability + concentrate gate
        from decision.market_direction import predict_direction
        import pandas as pd
        dscore = predict_direction(pd.read_parquet(cache))
        concentrate = CONCENTRATE_BY_MARKET.get(market.value, False)
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
                concentrate=concentrate, direction_score=dscore, stop_loss=STOP_LOSS,
            )
        print(f"[{market.value}] direction_score={dscore:+.4f} concentrate={concentrate} "
              f"→ concentrated={rep.concentrated}" if dscore is not None else
              f"[{market.value}] direction_score=None", flush=True)
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
