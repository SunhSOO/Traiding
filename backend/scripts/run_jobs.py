"""Trigger background jobs ad-hoc from the CLI.

Equivalent to APScheduler firing them, but synchronous and inline so
you can see the report immediately.

Usage:

    uv run python scripts/run_jobs.py technical
    uv run python scripts/run_jobs.py regime
    uv run python scripts/run_jobs.py decisions
    uv run python scripts/run_jobs.py all
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_technical() -> None:
    from core.db import session_scope
    from core.types import Market
    from technical.runner import score_market

    now = datetime.now(timezone.utc)
    for market in (Market.KR, Market.US):
        with session_scope() as s:
            report = score_market(s, market=market, as_of=now)
        print(f"  technical[{market.value}] "
              f"processed={report.tickers_processed} "
              f"failed={report.tickers_failed} "
              f"abstained={report.tickers_abstained}")


def run_regime() -> None:
    from core.db import session_scope
    from regime.runner import run_regime_daily

    with session_scope() as s:
        report = run_regime_daily(s)
    print(f"  regime classified={report.markets_classified} "
          f"failed={report.markets_failed}")
    if report.errors:
        for e in report.errors:
            print(f"    err: {e}")


def run_decisions() -> None:
    from core.db import session_scope
    from core.risk import RiskEngine, RiskLimits
    from core.types import Market
    from decision.runner import run_decisions
    from brokers.db_price_oracle import DBPriceOracle
    from brokers.paper import PaperBroker
    from brokers.paper_persistence import load_or_create_account, rehydrate_logic

    now = datetime.now(timezone.utc)
    risk_engine = RiskEngine()
    risk_limits = RiskLimits()
    oracle = DBPriceOracle(session_factory=session_scope)
    market_accounts = [
        (Market.KR, "default-kr", "KRW", 100_000_000.0),
        (Market.US, "default-us", "USD", 100_000.0),
    ]
    for market, account_name, base_ccy, initial_bal in market_accounts:
        with session_scope() as setup_session:
            account = load_or_create_account(
                setup_session, name=account_name,
                base_currency=base_ccy, initial_balance=initial_bal,
            )
            logic = rehydrate_logic(setup_session, account)
            account_id = account.id
        broker = PaperBroker(
            logic, oracle,
            session_factory=session_scope, account_id=account_id,
        )
        with session_scope() as s:
            report = run_decisions(
                s, market=market, as_of=now,
                broker=broker, risk_engine=risk_engine, risk_limits=risk_limits,
            )
        print(f"  decisions[{market.value}] "
              f"considered={report.tickers_considered} "
              f"traded={report.tickers_traded} "
              f"held={report.tickers_held} "
              f"rejected={report.tickers_rejected} "
              f"errored={report.tickers_errored}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["technical", "regime", "decisions", "all"])
    args = ap.parse_args()

    print(f"[run_jobs] {args.job} @ {datetime.now(timezone.utc).isoformat()}")
    if args.job in ("technical", "all"):
        print("[1] technical.score.daily")
        run_technical()
    if args.job in ("regime", "all"):
        print("[2] regime.daily")
        run_regime()
    if args.job in ("decisions", "all"):
        print("[3] decisions.daily")
        run_decisions()
    print("done.")


if __name__ == "__main__":
    main()
