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


def run_walk_forward(t_only: bool = False, min_conf: float = 0.40) -> None:
    import asyncio
    from datetime import timedelta
    from core.db import async_session_scope
    from core.types import Market
    from backtest.rescoring_runner import run_rescoring_backtest
    from core.models.backtest import BacktestRunRow

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=90)
    suffix = "-T-only" if t_only else ""
    label_root = f"walk-forward-{now.date().isoformat()}{suffix}"
    override = {"F": 0.0, "T": 1.0, "I": 0.0} if t_only else None

    async def _one(market: Market) -> None:
        initial = 100_000_000.0 if market is Market.KR else 100_000.0
        async with async_session_scope() as db:
            result = await run_rescoring_backtest(
                db, start=start, end=now,
                market=market.value, initial_balance=initial,
                position_fraction=0.05,
                use_learned_weights=(not t_only),
                weight_override=override,
                min_overall_confidence=min_conf,
            )
            final_equity = result.curve[-1].equity if result.curve else initial
            row = BacktestRunRow(
                label=f"{label_root}-{market.value}", mode="rescoring",
                market=market.value,
                window_start=start, window_end=now,
                initial_balance=initial, final_equity=float(final_equity),
                total_trades=result.summary.total_trades,
                win_rate=float(result.summary.win_rate),
                total_realized_pnl=float(result.summary.total_realized_pnl),
                avg_trade_pnl=float(result.summary.avg_trade_pnl),
                best_trade_pnl=float(result.summary.best_trade_pnl),
                worst_trade_pnl=float(result.summary.worst_trade_pnl),
                max_drawdown=float(result.summary.max_drawdown),
                sharpe_like=float(result.summary.sharpe_like),
                return_pct=float(result.summary.return_pct),
                skipped_signals=result.result.skipped_signals,
                config_snapshot={
                    "mode": "rescoring", "market": market.value,
                    "initial_balance": initial, "position_fraction": 0.05,
                    "use_learned_weights": True,
                },
                equity_points=[
                    {"date": pt.date.isoformat(), "equity": float(pt.equity),
                     "drawdown": float(pt.drawdown)}
                    for pt in result.curve
                ],
                triggered_by="manual:run_jobs",
            )
            db.add(row)
            await db.flush()
        print(f"  walk_forward[{market.value}] trades={result.summary.total_trades} "
              f"return_pct={result.summary.return_pct:.2f}% "
              f"win_rate={result.summary.win_rate:.2%} "
              f"sharpe={result.summary.sharpe_like:.3f} "
              f"mdd={result.summary.max_drawdown:.2%} "
              f"final_eq={float(final_equity):,.2f}")

    async def _all() -> None:
        for m in (Market.KR, Market.US):
            await _one(m)
    asyncio.run(_all())


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
                                     "fundamental", "training",
                                     "walk-forward", "all"])
    ap.add_argument("--max-articles", type=int, default=200)
    ap.add_argument("--t-only", action="store_true",
                    help="walk-forward only: force T=1.0 weight override")
    ap.add_argument("--min-conf", type=float, default=0.40,
                    help="walk-forward only: composite-confidence floor")
    args = ap.parse_args()

    print(f"[run_jobs] {args.job} @ {datetime.now(timezone.utc).isoformat()}")
    if args.job in ("fundamental", "all"):
        print("[fundamental.score.weekly]")
        run_fundamental()
    if args.job in ("training", "all"):
        print("[training.weekly]")
        run_training()
    if args.job in ("walk-forward", "all"):
        print("[backtest.walk_forward.weekly]")
        run_walk_forward(t_only=args.t_only, min_conf=args.min_conf)
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
