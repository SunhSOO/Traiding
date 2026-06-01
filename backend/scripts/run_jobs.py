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


def run_training() -> None:
    from core.db import session_scope
    from training.runner import run_training as _rt
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        report = _rt(s, as_of=now)
    print(f"  training markets={report.markets} "
          f"samples={report.samples_collected} "
          f"attempted={report.clusters_attempted} "
          f"trained={report.clusters_trained} "
          f"skipped={report.clusters_skipped_low_data}")
    for d in report.cluster_details[:10]:
        print(f"    cluster={d.get('cluster_id')} skipped={d.get('skipped')} "
              f"n={d.get('n_samples')} r2={d.get('r2')}")


def run_fundamental() -> None:
    from core.db import session_scope
    from core.types import Market
    from fundamental.runner import score_market as fund_score_market
    now = datetime.now(timezone.utc)
    for market in (Market.KR, Market.US):
        with session_scope() as s:
            report = fund_score_market(s, market=market, as_of=now)
        print(f"  fundamental[{market.value}] "
              f"processed={report.tickers_processed} "
              f"skipped={report.tickers_skipped_no_data}")


def run_info_classify(max_articles: int = 200) -> None:
    from core.db import session_scope
    from information.runner import classify_pending
    with session_scope() as s:
        report = classify_pending(s, max_articles=max_articles, lookback_days=30)
    print(f"  info.classify seen={report.articles_seen} "
          f"classified={report.articles_classified} "
          f"failed={report.articles_failed} model={report.model_version}")


def run_info_score() -> None:
    from core.db import session_scope
    from core.types import Market
    from information.runner import score_market as info_score_market
    now = datetime.now(timezone.utc)
    for market in (Market.KR, Market.US):
        with session_scope() as s:
            report = info_score_market(s, market=market, as_of=now)
        print(f"  info.score[{market.value}] "
              f"processed={report.tickers_processed} "
              f"skipped={report.tickers_skipped_no_mentions}")


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
    ap.add_argument("job", choices=["technical", "regime", "decisions",
                                     "info-classify", "info-score",
                                     "fundamental", "training", "all"])
    ap.add_argument("--max-articles", type=int, default=200)
    args = ap.parse_args()

    print(f"[run_jobs] {args.job} @ {datetime.now(timezone.utc).isoformat()}")
    if args.job in ("fundamental", "all"):
        print("[fundamental.score.weekly]")
        run_fundamental()
    if args.job in ("training", "all"):
        print("[training.weekly]")
        run_training()
    if args.job in ("info-classify", "all"):
        print("[info.classify]")
        run_info_classify(max_articles=args.max_articles)
    if args.job in ("info-score", "all"):
        print("[info.score]")
        run_info_score()
    if args.job in ("technical", "all"):
        print("[technical.score.daily]")
        run_technical()
    if args.job in ("regime", "all"):
        print("[regime.daily]")
        run_regime()
    if args.job in ("decisions", "all"):
        print("[decisions.daily]")
        run_decisions()
    print("done.")


if __name__ == "__main__":
    main()
