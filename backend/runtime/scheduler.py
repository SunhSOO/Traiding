"""APScheduler integration.

We register all production cron jobs here and start/stop the scheduler
from the FastAPI lifespan. AsyncIOScheduler shares the FastAPI event
loop — there is no separate worker process to run.

Job registration is data-driven (:class:`JobSpec`) so tests can
instantiate a scheduler with a custom job list and verify that the
right ones fire at the right times without actually triggering jobs.

Job catalog (all default to disabled in local dev unless RUNTIME_MODE
makes them safe):

| ID                  | Cron                | Action                          |
|---------------------|---------------------|---------------------------------|
| universe.monthly    | 0 6 1 * *  (KST)    | refresh KOSPI200/KOSDAQ150/SP500/NASDAQ100 |
| prices.kr.daily     | 35 15 * * 1-5 KST   | KR EOD bars (5 min after close) |
| prices.us.daily     | 5 16 * * 1-5 EST    | US EOD bars                     |
| macro.daily         | 30 6 * * *  KST     | FRED + ECOS refresh             |
| audit.compact       | 0 4 * * *   UTC     | (Phase 2+) compact old audit rows |
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from core.logging import get_logger

log = get_logger(__name__)

JobCallable = Callable[[], Awaitable[None]]


@dataclass
class JobSpec:
    """Declarative job description. Tests assert on these without
    starting a scheduler."""
    id: str
    func: JobCallable
    trigger: str        # 'cron' | 'interval'
    cron_kwargs: dict   # passed to APScheduler CronTrigger
    enabled: bool = True
    timezone: str = "UTC"


# ──────────────────────────────────────────────────────────────────────
# Default production jobs
# ──────────────────────────────────────────────────────────────────────


async def _job_news_backfill_chunk() -> None:
    """Chip away at the rolling-window news backfill.

    Job ID + window come from settings (operator-controlled). Each
    invocation runs at most 50 chunks (≈ 5–10 minutes) so the
    scheduler doesn't get stuck holding the loop. Resumes from
    ``BackfillProgress`` automatically."""
    from datetime import date, timedelta

    from core.config import get_settings
    from core.db import session_scope

    settings = get_settings()
    job_id = settings.news_backfill_job_id
    window_days = settings.news_backfill_window_days
    if not job_id:
        log.info("job.news_backfill.skipped_no_job_id")
        return

    end = date.today()
    start = end - timedelta(days=window_days)
    with session_scope() as s:
        # Lazy import — historical_runner pulls in adapters that need
        # network libs we don't want loaded on every scheduler import.
        from data.news.historical_runner import run_historical_backfill

        summary = run_historical_backfill(
            s, job_id=job_id, start=start, end=end, max_chunks=50,
        )
        log.info(
            "job.news_backfill",
            job_id=job_id, ran=summary["ran"], succeeded=summary["succeeded"],
            failed=summary["failed"], rows=summary["rows_inserted"],
            remaining=summary["next_chunks_remaining"],
        )


async def _job_universe_refresh() -> None:
    from datetime import date
    from core.db import session_scope
    from data.universe import sync_universe
    from data.universe.kr_universe import build_default_kr_fetcher
    from data.universe.us_universe import build_default_us_fetcher

    with session_scope() as s:
        reports = sync_universe(
            s, as_of_date=date.today(),
            kr_fetchers=build_default_kr_fetcher(),
            us_fetchers=build_default_us_fetcher(),
        )
        for r in reports:
            log.info("job.universe", market=r.market.value, fetched=r.fetched_count, error=r.error)


async def _job_kr_prices_daily() -> None:
    from datetime import date, timedelta
    from core.db import session_scope
    from core.types import Market
    from data.price import sync_daily_prices

    today = date.today()
    with session_scope() as s:
        report = sync_daily_prices(
            s, market=Market.KR,
            start=today - timedelta(days=5), end=today,
        )
        log.info(
            "job.prices_kr",
            processed=report.tickers_processed,
            failed=report.tickers_failed, rows=report.rows_upserted,
        )


async def _job_us_prices_daily() -> None:
    from datetime import date, timedelta
    from core.db import session_scope
    from core.types import Market
    from data.price import sync_daily_prices

    today = date.today()
    with session_scope() as s:
        report = sync_daily_prices(
            s, market=Market.US,
            start=today - timedelta(days=5), end=today,
        )
        log.info(
            "job.prices_us",
            processed=report.tickers_processed,
            failed=report.tickers_failed, rows=report.rows_upserted,
        )


async def _job_macro_daily() -> None:
    from datetime import date, timedelta
    from core.db import session_scope
    from data.macro import sync_macro_series
    from data.macro.loader import default_routing_fetcher

    today = date.today()
    with session_scope() as s:
        # Pull last 30 days every run — cheap, picks up FRED revisions.
        report = sync_macro_series(
            s, start=today - timedelta(days=30), end=today,
            series_codes=[
                "RATE_US_FFR", "RATE_US_10Y", "VIX", "FX_DXY",
                "FX_USDKRW", "RATE_KR_BASE", "IDX_KOSPI_ECOS",
            ],
            fetcher=default_routing_fetcher,
        )
        log.info(
            "job.macro",
            processed=report.series_processed, failed=report.series_failed,
            rows=report.rows_upserted,
        )


async def _job_disclosures_kr_daily() -> None:
    """KR DART filings — pull last 7 days for every active KR ticker
    that has a corp_code populated by the universe.monthly enrichment."""
    from datetime import date, timedelta
    from sqlalchemy import select

    from core.db import session_scope
    from core.models.universe import Security
    from core.types import Market
    from data.disclosures import sync_disclosures
    from data.disclosures.kr_dart import fetch_kr_disclosures

    today = date.today()
    start = today - timedelta(days=7)
    list_fetcher = _build_dart_list_fetcher()

    with session_scope() as s:
        corp_map = {
            row.ticker: row.corp_code
            for row in s.scalars(
                select(Security).where(Security.market == "KR", Security.is_active.is_(True))
            )
            if row.corp_code
        }
        if not corp_map:
            log.warning("job.disclosures_kr.no_corp_codes",
                        note="universe.monthly hasn't enriched yet; run it first")
            return

        def _fetch_for(ticker: str):
            corp = corp_map.get(ticker)
            if not corp:
                return []
            return fetch_kr_disclosures(
                corp_code=corp, ticker=ticker,
                start=start, end=today,
                fetch_list=list_fetcher,
            )

        report = sync_disclosures(
            s, market=Market.KR,
            tickers=list(corp_map.keys()), fetcher=_fetch_for,
        )
        log.info(
            "job.disclosures_kr",
            processed=report.tickers_processed, failed=report.tickers_failed,
            inserted=report.rows_inserted,
        )


def _build_dart_list_fetcher():
    """(corp_code, start, end, kind) → list[dict] wrapper around
    OpenDartReader.list. Returns empty on any error so one bad ticker
    can't break the batch."""
    try:
        from core.config import get_settings
        import OpenDartReader

        key = get_settings().dart_api_key.get_secret_value()
        if not key:
            return lambda *a, **k: []
        dart = OpenDartReader(key)

        def _fetch(corp_code, start, end, kind=None):
            try:
                df = dart.list(corp_code, start=start.strftime("%Y%m%d"),
                               end=end.strftime("%Y%m%d"), kind=kind)
                if df is None or df.empty:
                    return []
                return df.to_dict("records")
            except Exception:
                return []
        return _fetch
    except Exception:
        return lambda *a, **k: []


async def _job_disclosures_us_daily() -> None:
    """US EDGAR submissions for each active US ticker that has a CIK on file."""
    from core.db import session_scope
    from core.models.universe import Security
    from core.types import Market
    from data.disclosures import sync_disclosures
    from data.disclosures.us_edgar import fetch_us_disclosures
    from sqlalchemy import select

    with session_scope() as s:
        cik_map = {
            row.ticker: row.cik
            for row in s.scalars(
                select(Security).where(Security.market == "US", Security.is_active.is_(True))
            )
            if row.cik
        }

        def _fetch_for(ticker: str):
            cik = cik_map.get(ticker)
            return fetch_us_disclosures(cik=cik, ticker=ticker) if cik else []

        report = sync_disclosures(
            s, market=Market.US, tickers=list(cik_map.keys()), fetcher=_fetch_for,
        )
        log.info(
            "job.disclosures_us",
            processed=report.tickers_processed, failed=report.tickers_failed,
            inserted=report.rows_inserted,
        )


async def _job_financials_monthly() -> None:
    """Mid-month batch — financials catch-up for KR (DART) + US (EDGAR).

    KR: requires corp_code; iterates active KR tickers, pulls the
        latest 4 reporting periods.
    US: requires cik; iterates active US tickers, pulls all configured
        concepts (the XBRL companyconcept API returns full history per
        call so we don't need per-period iteration).
    """
    from datetime import datetime
    from sqlalchemy import select

    from core.db import session_scope
    from core.models.universe import Security
    from core.types import Market
    from data.fundamental import sync_financials

    now = datetime.now()

    # ── KR ──
    try:
        from data.fundamental.kr_dart import (
            build_default_dart_fetcher, fetch_kr_financials,
        )
        kr_fetch = build_default_dart_fetcher()
    except Exception as e:
        log.warning("job.financials_kr.skipped", error=str(e))
        kr_fetch = None

    if kr_fetch is not None:
        with session_scope() as s:
            corp_map = {
                r.ticker: r.corp_code
                for r in s.scalars(
                    select(Security).where(Security.market == "KR", Security.is_active.is_(True))
                )
                if r.corp_code
            }
            if not corp_map:
                log.warning("job.financials_kr.no_corp_codes")
            else:
                # Cover the latest 2 quarters + last annual
                current_year = now.year
                periods = [(current_year, p) for p in ("Q1", "Q2", "Q3")] + [(current_year - 1, "ANNUAL")]

                def _kr_fetch_for_ticker(ticker: str):
                    rows = []
                    corp = corp_map.get(ticker)
                    if not corp:
                        return rows
                    for year, period_kind in periods:
                        rows.extend(fetch_kr_financials(
                            corp_code=corp, ticker=ticker,
                            year=year, period_kind=period_kind,
                            fetch_finstate=kr_fetch,
                        ))
                    return rows

                report_kr = sync_financials(
                    s, market=Market.KR,
                    tickers=list(corp_map.keys()),
                    fetcher=_kr_fetch_for_ticker,
                )
                log.info(
                    "job.financials_kr",
                    processed=report_kr.tickers_processed,
                    failed=report_kr.tickers_failed,
                    rows=report_kr.rows_upserted,
                )

    # ── US ──
    try:
        from data.fundamental.us_edgar import fetch_us_financials
    except Exception as e:
        log.warning("job.financials_us.skipped", error=str(e))
        return

    with session_scope() as s:
        cik_map = {
            r.ticker: r.cik
            for r in s.scalars(
                select(Security).where(Security.market == "US", Security.is_active.is_(True))
            )
            if r.cik
        }
        if not cik_map:
            log.warning("job.financials_us.no_ciks")
            return

        def _us_fetch_for_ticker(ticker: str):
            cik = cik_map.get(ticker)
            if not cik:
                return []
            return fetch_us_financials(cik=cik, ticker=ticker)

        report_us = sync_financials(
            s, market=Market.US,
            tickers=list(cik_map.keys()),
            fetcher=_us_fetch_for_ticker,
        )
        log.info(
            "job.financials_us",
            processed=report_us.tickers_processed,
            failed=report_us.tickers_failed,
            rows=report_us.rows_upserted,
        )


async def _job_news_intraday_rss() -> None:
    """Every 15 minutes: pull the default RSS feed list. Cheap (~10 GETs)."""
    from core.db import session_scope
    from data.news import sync_news
    from data.news.loader import NewsSourceSpec
    from data.news.rss import fetch_all_default_feeds

    with session_scope() as s:
        report = sync_news(
            s,
            sources=[NewsSourceSpec(name="rss", fetcher=fetch_all_default_feeds)],
        )
        log.info(
            "job.news_rss",
            seen=report.articles_seen, inserted=report.articles_inserted,
            mentions=report.mentions_inserted, errors=len(report.errors),
        )


async def _job_news_daily_kr() -> None:
    """Daily KR news refresh — BIGKinds + Naver Search per active ticker.

    Naver free quota is 25,000 calls/day. With ~350 active KR tickers
    this job uses ~350 calls (one search per ticker name), well within
    quota. BIGKinds has a daily transfer cap that this volume comfortably
    fits under.
    """
    from datetime import date, timedelta
    from sqlalchemy import select

    from core.db import session_scope
    from core.models.universe import Security
    from data.news import sync_news
    from data.news.bigkinds import fetch_bigkinds
    from data.news.loader import NewsSourceSpec
    from data.news.naver_search import fetch_naver_news

    today = date.today()
    bigkinds_start = today - timedelta(days=2)

    with session_scope() as s:
        kr_names = {
            r.ticker: r.name for r in s.scalars(
                select(Security).where(Security.market == "KR", Security.is_active.is_(True))
            )
            if r.name
        }
        if not kr_names:
            log.warning("job.news_kr.no_active_tickers")
            return

        def _fetch_bigkinds():
            out = []
            for ticker, name in kr_names.items():
                out.extend(fetch_bigkinds(
                    query=name, start=bigkinds_start, end=today,
                    hard_limit=20,    # cap per ticker
                ))
            return out

        def _fetch_naver():
            out = []
            for ticker, name in kr_names.items():
                out.extend(fetch_naver_news(query=name, display=20, max_pages=1))
            return out

        report = sync_news(
            s,
            sources=[
                NewsSourceSpec(name="bigkinds", fetcher=_fetch_bigkinds),
                NewsSourceSpec(name="naver_search", fetcher=_fetch_naver),
            ],
        )
        log.info(
            "job.news_kr",
            seen=report.articles_seen, inserted=report.articles_inserted,
            mentions=report.mentions_inserted,
            sources_failed=report.sources_failed,
        )


async def _job_news_daily_global() -> None:
    """Daily GDELT pull for the prior 24h, broad-query for US tickers."""
    from datetime import timedelta
    from core.db import session_scope
    from data.news import sync_news
    from data.news.gdelt import fetch_gdelt
    from data.news.loader import NewsSourceSpec
    from datetime import UTC, datetime

    end = datetime.now(UTC)
    start = end - timedelta(days=1)

    def _fetch():
        # Broad query — Phase 2 will narrow per ticker once we have
        # an active US universe loaded. For now we capture "stocks" /
        # "earnings" / "market" so the table starts populating.
        return fetch_gdelt(
            query="(stocks OR earnings OR markets) sourcelang:English",
            start=start, end=end, languages=["English"],
        )

    with session_scope() as s:
        report = sync_news(
            s,
            sources=[NewsSourceSpec(name="gdelt", fetcher=_fetch)],
        )
        log.info(
            "job.news_global",
            seen=report.articles_seen, inserted=report.articles_inserted,
            mentions=report.mentions_inserted,
        )


async def _job_training_weekly() -> None:
    """Retrain per-cluster (w_F, w_T, w_I) weights every Sunday.

    Picks up the last ``window_days`` of paired (module_scores, daily_prices)
    data, fits OLS per cluster, persists to ``cluster_weights``. The
    decision engine reads the latest row on its next run.
    """
    from datetime import UTC, datetime
    from core.db import session_scope
    from training.runner import run_training

    with session_scope() as s:
        report = run_training(s, as_of=datetime.now(UTC))
        log.info(
            "job.training_weekly",
            samples=report.samples_collected,
            trained=report.clusters_trained,
            skipped=report.clusters_skipped_low_data,
            markets=report.markets,
        )


async def _job_mlops_retrain_weekly() -> None:
    """Full MLOps retrain — LGBM/XGB/CatBoost (default + tune), then
    forward-selection ensemble rebuild via scripts/mlops_retrain.py.

    Runs 2 hours after training.weekly so legacy OLS cluster_weights are
    already up to date when the LightGBM registry rebuilds.
    """
    import subprocess
    from pathlib import Path
    cwd = Path(__file__).resolve().parents[1]
    log.info("job.mlops_retrain.start")
    proc = subprocess.run(
        ["uv", "run", "python", "scripts/mlops_retrain.py", "--markets", "KR,US"],
        cwd=str(cwd), capture_output=True, text=True, timeout=4 * 3600,
    )
    log.info(
        "job.mlops_retrain.done",
        rc=proc.returncode,
        stdout_tail=(proc.stdout or "")[-2000:],
        stderr_tail=(proc.stderr or "")[-1000:],
    )


async def _job_drift_check_daily() -> None:
    """Daily drift check on active EnsembleSpec via KS-test + rolling IC.

    Cheap (~5 minutes); if any cluster drifts the operator can flip a flag
    or wait for the next mlops_retrain.weekly cycle."""
    import subprocess
    from pathlib import Path
    cwd = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["uv", "run", "python", "scripts/mlops_retrain.py",
         "--drift-only", "--markets", "KR,US"],
        cwd=str(cwd), capture_output=True, text=True, timeout=30 * 60,
    )
    log.info(
        "job.drift_check.done",
        rc=proc.returncode,
        stdout_tail=(proc.stdout or "")[-1000:],
    )


async def _job_regime_daily() -> None:
    """Re-classify market regime each morning right after the macro
    daily refresh. Reads macro_series and writes one row per market
    into ``market_regime``. Idempotent."""
    from datetime import UTC, datetime
    from core.db import session_scope
    from regime.runner import run_regime_daily

    now = datetime.now(UTC)
    with session_scope() as s:
        report = run_regime_daily(s, as_of=now)
        log.info(
            "job.regime_daily",
            classified=report.markets_classified,
            failed=report.markets_failed,
        )


async def _job_walk_forward_weekly() -> None:
    """Weekly walk-forward backtest persisted to ``backtest_runs``.

    Fires after the training job lands. Re-scores the last 90 days
    using the freshly-learned cluster weights for each market and
    saves the result with a ``walk-forward-{date}-{market}`` label.
    Comparison view in the backtest page picks these up automatically.

    Disabled by default — the operator flips it on after Phase 3
    learning has produced enough rows to be useful."""
    from datetime import UTC, datetime, timedelta

    from core.db import async_session_scope
    from core.types import Market
    from backtest.rescoring_runner import run_rescoring_backtest
    from core.models.backtest import BacktestRunRow

    now = datetime.now(UTC)
    start = now - timedelta(days=90)
    label_root = f"walk-forward-{now.date().isoformat()}"

    async def _run_one(market: Market) -> None:
        async with async_session_scope() as db:
            try:
                result = await run_rescoring_backtest(
                    db,
                    start=start, end=now,
                    market=market.value,
                    initial_balance=(
                        100_000_000.0 if market is Market.KR else 100_000.0
                    ),
                    position_fraction=0.05,
                    use_learned_weights=True,
                )
                final_equity = (
                    result.curve[-1].equity if result.curve
                    else (100_000_000.0 if market is Market.KR else 100_000.0)
                )
                initial_balance = (
                    100_000_000.0 if market is Market.KR else 100_000.0
                )
                row = BacktestRunRow(
                    label=f"{label_root}-{market.value}",
                    mode="rescoring",
                    market=market.value,
                    window_start=start, window_end=now,
                    initial_balance=initial_balance,
                    final_equity=float(final_equity),
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
                        "initial_balance": initial_balance,
                        "position_fraction": 0.05,
                        "use_learned_weights": True,
                    },
                    equity_points=[
                        {"date": pt.date.isoformat(),
                         "equity": float(pt.equity),
                         "drawdown": float(pt.drawdown)}
                        for pt in result.curve
                    ],
                    triggered_by="scheduler:walk_forward",
                )
                db.add(row)
                await db.flush()    # commit happens at context-manager exit
                log.info(
                    "job.walk_forward.persisted",
                    market=market.value,
                    trades=result.summary.total_trades,
                    return_pct=result.summary.return_pct,
                )
            except Exception as e:
                log.exception("job.walk_forward.failed", market=market.value, error=str(e))

    for m in (Market.KR, Market.US):
        await _run_one(m)


async def _job_info_classify_hourly() -> None:
    """Classify up to N pending articles each hour. GPU-bound."""
    from core.db import session_scope
    from information.runner import classify_pending

    with session_scope() as s:
        report = classify_pending(s, max_articles=50, lookback_days=7)
        log.info(
            "job.info_classify",
            seen=report.articles_seen,
            classified=report.articles_classified,
            failed=report.articles_failed,
            model=report.model_version,
        )


async def _job_info_score_daily() -> None:
    """Compute per-ticker information scores for both markets."""
    from datetime import UTC, datetime
    from core.db import session_scope
    from core.types import Market
    from information.runner import score_market as info_score_market

    now = datetime.now(UTC)
    with session_scope() as s:
        for market in (Market.KR, Market.US):
            report = info_score_market(s, market=market, as_of=now)
            log.info(
                "job.info_score",
                market=market.value,
                processed=report.tickers_processed,
                skipped=report.tickers_skipped_no_mentions,
                rows=report.rows_written,
            )


async def _job_technical_score_daily() -> None:
    """Daily technical scoring for both markets, fires after both
    closes (KR 15:35 KST + US 16:05 EST + a buffer)."""
    from datetime import UTC, datetime
    from core.db import session_scope
    from core.types import Market
    from technical.runner import score_market as tech_score_market

    now = datetime.now(UTC)
    with session_scope() as s:
        for market in (Market.KR, Market.US):
            report = tech_score_market(s, market=market, as_of=now)
            log.info(
                "job.technical_score",
                market=market.value,
                processed=report.tickers_processed,
                failed=report.tickers_failed,
                abstained=report.tickers_abstained,
            )


async def _job_decisions_daily() -> None:
    """Fire the composite decision engine for both markets.

    Reads latest F/T/I scores → composite → gates → sizer → risk →
    paper broker → DecisionAudit. Production cadence is daily but
    cron can be tightened per-market once live trading is enabled."""
    from datetime import UTC, datetime
    from core.db import session_scope
    from core.risk import RiskEngine, RiskLimits
    from core.types import Market
    from decision.runner import run_decisions

    from brokers.db_price_oracle import DBPriceOracle
    from brokers.paper import PaperBroker
    from brokers.paper_persistence import load_or_create_account, rehydrate_logic
    from core.db import session_scope

    now = datetime.now(UTC)
    risk_engine = RiskEngine()
    risk_limits = RiskLimits()    # defaults; per-market overrides land in Phase 6

    # Per-market account routing. Each market runs against its own
    # currency-matched paper account so the decision runner never has
    # to convert FX inside the sizer. Idempotent bootstrap; reuses any
    # operator-renamed account if those names are kept.
    market_accounts: list[tuple[Market, str, str, float]] = [
        (Market.KR, "default-kr", "KRW", 100_000_000.0),
        (Market.US, "default-us", "USD", 100_000.0),
    ]
    oracle = DBPriceOracle(session_factory=session_scope)

    for market, account_name, base_ccy, initial_bal in market_accounts:
        try:
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
                log.info(
                    "job.decisions",
                    market=market.value, account=account_name,
                    considered=report.tickers_considered,
                    traded=report.tickers_traded,
                    held=report.tickers_held,
                    rejected=report.tickers_rejected,
                    errored=report.tickers_errored,
                )
        except Exception as e:
            log.exception(
                "job.decisions.failed", market=market.value, error=str(e)
            )


async def _job_fundamental_score_weekly() -> None:
    """Weekly fundamental scoring — financials don't change daily, so
    weekly cadence keeps GPU/CPU free for the higher-frequency jobs."""
    from datetime import UTC, datetime
    from core.db import session_scope
    from core.types import Market
    from fundamental.runner import score_market as fund_score_market

    now = datetime.now(UTC)
    with session_scope() as s:
        for market in (Market.KR, Market.US):
            report = fund_score_market(s, market=market, as_of=now)
            log.info(
                "job.fundamental_score",
                market=market.value,
                processed=report.tickers_processed,
                skipped=report.tickers_skipped_no_data,
            )


DEFAULT_JOBS: list[JobSpec] = [
    JobSpec(
        id="universe.monthly",
        func=_job_universe_refresh,
        trigger="cron",
        cron_kwargs={"day": 1, "hour": 6, "minute": 0},
        timezone="Asia/Seoul",
    ),
    JobSpec(
        id="prices.kr.daily",
        func=_job_kr_prices_daily,
        trigger="cron",
        cron_kwargs={"day_of_week": "mon-fri", "hour": 15, "minute": 35},
        timezone="Asia/Seoul",
    ),
    JobSpec(
        id="prices.us.daily",
        func=_job_us_prices_daily,
        trigger="cron",
        cron_kwargs={"day_of_week": "mon-fri", "hour": 16, "minute": 5},
        timezone="America/New_York",
    ),
    JobSpec(
        id="macro.daily",
        func=_job_macro_daily,
        trigger="cron",
        cron_kwargs={"hour": 6, "minute": 30},
        timezone="Asia/Seoul",
    ),
    JobSpec(
        id="disclosures.kr.daily",
        func=_job_disclosures_kr_daily,
        trigger="cron",
        cron_kwargs={"day_of_week": "mon-fri", "hour": 19, "minute": 0},
        timezone="Asia/Seoul",
    ),
    JobSpec(
        id="disclosures.us.daily",
        func=_job_disclosures_us_daily,
        trigger="cron",
        cron_kwargs={"day_of_week": "mon-fri", "hour": 17, "minute": 30},
        timezone="America/New_York",
    ),
    JobSpec(
        id="financials.monthly",
        func=_job_financials_monthly,
        trigger="cron",
        # Mid-month each month covers both KR (45-day) and US (40-day)
        # post-period deadlines.
        cron_kwargs={"day": 15, "hour": 7, "minute": 0},
        timezone="Asia/Seoul",
    ),
    JobSpec(
        id="news.intraday.rss",
        func=_job_news_intraday_rss,
        trigger="cron",
        cron_kwargs={"minute": "*/15"},
        timezone="UTC",
    ),
    JobSpec(
        id="news.daily.kr",
        func=_job_news_daily_kr,
        trigger="cron",
        cron_kwargs={"hour": 6, "minute": 45},
        timezone="Asia/Seoul",
    ),
    JobSpec(
        id="news.daily.global",
        func=_job_news_daily_global,
        trigger="cron",
        cron_kwargs={"hour": 1, "minute": 0},
        timezone="UTC",
    ),
    JobSpec(
        id="information.classify.hourly",
        func=_job_info_classify_hourly,
        trigger="cron",
        cron_kwargs={"minute": 15},   # at :15 every hour
        timezone="UTC",
    ),
    JobSpec(
        id="information.score.daily",
        func=_job_info_score_daily,
        trigger="cron",
        cron_kwargs={"hour": 22, "minute": 0},
        timezone="UTC",
    ),
    JobSpec(
        id="technical.score.daily",
        func=_job_technical_score_daily,
        trigger="cron",
        cron_kwargs={"hour": 21, "minute": 30},  # after both market closes
        timezone="UTC",
    ),
    JobSpec(
        id="fundamental.score.weekly",
        func=_job_fundamental_score_weekly,
        trigger="cron",
        cron_kwargs={"day_of_week": "sat", "hour": 3, "minute": 0},
        timezone="UTC",
    ),
    JobSpec(
        id="decisions.daily",
        func=_job_decisions_daily,
        trigger="cron",
        cron_kwargs={"hour": 22, "minute": 30},   # after all scoring jobs finish
        timezone="UTC",
    ),
    JobSpec(
        id="training.weekly",
        func=_job_training_weekly,
        trigger="cron",
        cron_kwargs={"day_of_week": "sun", "hour": 4, "minute": 0},
        timezone="UTC",
    ),
    JobSpec(
        # Daily regime classifier. Runs 15 minutes after macro.daily so
        # the latest FRED / ECOS rows are available. Cheap (a few
        # SELECTs on macro_series + one UPSERT per market).
        id="regime.daily",
        func=_job_regime_daily,
        trigger="cron",
        cron_kwargs={"hour": 6, "minute": 45},
        timezone="Asia/Seoul",
    ),
    JobSpec(
        # Persisted walk-forward validation. Runs every Sunday at 05:00
        # UTC — one hour after training.weekly so it uses the fresh
        # cluster weights. Result lands in ``backtest_runs`` and the
        # backtest page comparison view picks it up.
        id="backtest.walk_forward.weekly",
        func=_job_walk_forward_weekly,
        trigger="cron",
        cron_kwargs={"day_of_week": "sun", "hour": 5, "minute": 0},
        timezone="UTC",
        enabled=False,
    ),
    JobSpec(
        # Full MLOps retrain — LightGBM/XGBoost/CatBoost (default + tune
        # per cluster size) → forward-selection ensemble rebuild → spec
        # auto-deploy with rollback. Runs Sunday 06:00 UTC, ~2h after
        # training.weekly so legacy OLS weights are fresh first.
        id="mlops.retrain.weekly",
        func=_job_mlops_retrain_weekly,
        trigger="cron",
        cron_kwargs={"day_of_week": "sun", "hour": 6, "minute": 0},
        timezone="UTC",
        enabled=False,    # operator flips on after first manual run
    ),
    JobSpec(
        # Daily drift check — KS-test on prediction distribution + rolling
        # IC of active EnsembleSpec. Cheap (~5 min). Logs warning when
        # cluster drifts; off-cycle retrain decision left to operator.
        id="mlops.drift_check.daily",
        func=_job_drift_check_daily,
        trigger="cron",
        cron_kwargs={"hour": 7, "minute": 0},
        timezone="UTC",
        enabled=False,
    ),
    JobSpec(
        # Chips at the rolling historical news backfill — keeps depth
        # fresh as time marches on. Disabled by default; the operator
        # flips it on after choosing an initial backfill window via
        # NEWS_BACKFILL_JOB_ID / NEWS_BACKFILL_WINDOW_DAYS.
        id="news.backfill.nightly",
        func=_job_news_backfill_chunk,
        trigger="cron",
        cron_kwargs={"hour": 3, "minute": 30},
        timezone="UTC",
        enabled=False,
    ),
]


# ──────────────────────────────────────────────────────────────────────
# Lifecycle
# ──────────────────────────────────────────────────────────────────────


class WoonamScheduler:
    """Thin wrapper around APScheduler's AsyncIOScheduler. Owns the
    lifecycle, exposes :meth:`status` for the ingestion UI, and lets
    tests pass a custom job list."""

    def __init__(self, jobs: Optional[list[JobSpec]] = None):
        self._jobs = jobs if jobs is not None else DEFAULT_JOBS
        self._scheduler = None  # type: ignore[assignment]
        self._started = False

    def start(self) -> None:
        """Idempotent. Re-entry is a no-op."""
        if self._started:
            return
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        self._scheduler = AsyncIOScheduler()
        for spec in self._jobs:
            if not spec.enabled:
                continue
            if spec.trigger != "cron":
                raise NotImplementedError(f"trigger {spec.trigger!r} not yet supported")
            trigger = CronTrigger(timezone=spec.timezone, **spec.cron_kwargs)
            self._scheduler.add_job(
                spec.func, trigger, id=spec.id,
                replace_existing=True, misfire_grace_time=300,
            )
        self._scheduler.start()
        self._started = True
        log.info("scheduler.started", jobs=[s.id for s in self._jobs if s.enabled])

    def shutdown(self) -> None:
        if not self._started or self._scheduler is None:
            return
        self._scheduler.shutdown(wait=False)
        self._started = False
        log.info("scheduler.stopped")

    def pause_all(self) -> int:
        """Pause every registered job. Returns count of jobs paused.

        Idempotent — calling on an already-paused scheduler is a no-op
        and still returns the count (so the kill switch can report on
        what's currently held)."""
        if self._scheduler is None:
            return 0
        count = 0
        for job in self._scheduler.get_jobs():
            try:
                job.pause()
                count += 1
            except Exception:
                pass
        log.warning("scheduler.paused_all", count=count)
        return count

    def resume_all(self) -> int:
        """Resume every paused job. Returns count of jobs resumed."""
        if self._scheduler is None:
            return 0
        count = 0
        for job in self._scheduler.get_jobs():
            try:
                job.resume()
                count += 1
            except Exception:
                pass
        log.info("scheduler.resumed_all", count=count)
        return count

    def is_paused(self) -> bool:
        """True if EVERY enabled job is paused (next_run_time is None)."""
        if self._scheduler is None:
            return False
        jobs = self._scheduler.get_jobs()
        if not jobs:
            return False
        return all(j.next_run_time is None for j in jobs)

    def status(self) -> list[dict]:
        """Snapshot of registered jobs and their next-fire times."""
        if self._scheduler is None:
            return [{"id": s.id, "enabled": s.enabled, "next_run": None} for s in self._jobs]
        return [
            {
                "id": job.id,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
            for job in self._scheduler.get_jobs()
        ]

    async def run_job_now(self, job_id: str) -> None:
        """Trigger one of the registered jobs immediately. Used by the
        ingestion UI for manual catch-up."""
        for spec in self._jobs:
            if spec.id == job_id:
                await spec.func()
                return
        raise KeyError(f"unknown job: {job_id!r}")
