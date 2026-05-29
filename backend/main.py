"""
woonam-auto-trading — FastAPI backend entry point.

Wiring responsibilities:

- Load settings (core.config) and configure structured logging (core.logging).
- Build the FastAPI app with a tight CORS policy from settings.
- On startup (lifespan):
    1. Bootstrap the operator user if the users table is empty.
    2. Initialise the MT5 bridge (FX/gold venue — equity is paper-broker).
    3. Initialise the Red-Green technical-signal runtime (legacy XAUUSD strategy).
- Register API routers (auth + existing strategy/account/etc.).
- Serve the legacy static frontend (will be replaced by KR/US-tabbed UI in Phase 5).
- WebSocket tick stream stays for the existing dashboard.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.config import RuntimeMode, get_settings
from core.logging import configure_logging, get_logger
from routes.account import router as account_router
from routes.admin import router as admin_router
from routes.analysis import router as analysis_router
from routes.attribution import router as attribution_router
from routes.auth import router as auth_router
from routes.backfill import router as backfill_router
from routes.config import router as config_router
from routes.data_quality import router as data_quality_router
from routes.health_summary import router as health_summary_router
from routes.backtest import router as backtest_router
from routes.decision import router as decision_router
from routes.drift import router as drift_router
from routes.history import router as history_router
from routes.ingestion import router as ingestion_router
from routes.llm import router as llm_router
from routes.macro import router as macro_router
from routes.news import router as news_router
from routes.overrides import router as overrides_router
from routes.paper import router as paper_router
from routes.regime import router as regime_router
from routes.risk import router as risk_router
from routes.scan import router as scan_router
from routes.strategy import router as strategy_router
from routes.trading import router as trading_router
from routes.training import router as training_router
from routes.universe import router as universe_router
from runtime.scheduler import WoonamScheduler
from technical.red_green_runtime import RedGreenRuntime
from websocket_manager import ConnectionManager

configure_logging()
log = get_logger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Process-lifetime setup + teardown."""
    settings = get_settings()
    log.info(
        "server.startup",
        runtime_mode=settings.runtime_mode.value,
        app_env=settings.app_env.value,
        llm_providers=settings.configured_llm_providers,
    )

    # ── Bootstrap operator user + default paper account (idempotent) ──
    try:
        from brokers.paper_persistence import load_or_create_account
        from core.db import session_scope
        from core.users import bootstrap_if_empty

        with session_scope() as s:
            bootstrap_if_empty(s)
            # Bootstrap one paper account per market so KR (KRW) and US (USD)
            # trades never mix currencies on the same ledger. The decision
            # runner refuses currency-mismatched orders by design — these
            # accounts are how the operator gets matched ledgers from day one.
            # Idempotent.
            load_or_create_account(
                s, name="default-kr", base_currency="KRW",
                initial_balance=100_000_000.0,    # ₩100M starting equity
            )
            load_or_create_account(
                s, name="default-us", base_currency="USD",
                initial_balance=100_000.0,        # $100k starting equity
            )
    except Exception as e:
        log.warning("bootstrap.skipped_due_to_db_error", error=str(e))

    # ── MT5 bridge (FX/gold only) ──
    try:
        from brokers.mt5 import MT5Bridge

        bridge = MT5Bridge()
        if bridge.connect():
            log.info("mt5.connected")
            app.state.mt5 = bridge
        else:
            log.warning("mt5.unavailable", note="running without MT5; FX/gold strategies will dry-run")
            app.state.mt5 = None
    except Exception as e:
        log.warning("mt5.init_failed", error=str(e))
        app.state.mt5 = None

    # ── Red-Green legacy runtime (kept as one technical signal) ──
    app.state.red_green_runtime = RedGreenRuntime(app.state.mt5)

    # ── Scheduler — registers ingestion cron jobs ──
    try:
        sched = WoonamScheduler()
        sched.start()
        app.state.scheduler = sched
    except Exception as e:
        log.warning("scheduler.start_failed", error=str(e))
        app.state.scheduler = None

    # Live mode requires explicit operator action; we never auto-start the strategy loop.
    if settings.runtime_mode == RuntimeMode.LIVE:
        log.warning(
            "live.mode.active",
            note="RUNTIME_MODE=live is set. Strategy loop NOT auto-started; operator must explicitly start.",
        )

    yield

    # ── Cleanup ──
    if getattr(app.state, "scheduler", None):
        app.state.scheduler.shutdown()
    if getattr(app.state, "red_green_runtime", None):
        await app.state.red_green_runtime.stop()
    if getattr(app.state, "mt5", None):
        app.state.mt5.disconnect()
        log.info("mt5.disconnected")
    log.info("server.shutdown")


# ── App ──
settings = get_settings()

app = FastAPI(
    title="woonam-auto-trading",
    description="KR+US equity auto-trading driven by composite F/T/I analysis",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — tightened to operator-configured list, no wildcard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

ws_manager = ConnectionManager()

# ── Routers ──
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(account_router, prefix="/api/account", tags=["Account"])
app.include_router(trading_router, prefix="/api/trading", tags=["Trading"])
app.include_router(history_router, prefix="/api/history", tags=["History"])
app.include_router(strategy_router, prefix="/api/strategy", tags=["Strategy"])
app.include_router(universe_router, prefix="/api/universe", tags=["Universe"])
app.include_router(ingestion_router, prefix="/api/ingestion", tags=["Ingestion"])
app.include_router(decision_router, prefix="/api/decision", tags=["Decision"])
app.include_router(analysis_router, prefix="/api/analysis", tags=["Analysis"])
app.include_router(paper_router, prefix="/api/paper", tags=["Paper"])
app.include_router(training_router, prefix="/api/training", tags=["Training"])
app.include_router(drift_router, prefix="/api/drift", tags=["Drift"])
app.include_router(admin_router, prefix="/api/admin", tags=["Admin"])
app.include_router(backtest_router, prefix="/api/backtest", tags=["Backtest"])
app.include_router(backfill_router, prefix="/api/backfill", tags=["Backfill"])
app.include_router(overrides_router, prefix="/api/overrides", tags=["Overrides"])
app.include_router(risk_router, prefix="/api/risk", tags=["Risk"])
app.include_router(news_router, prefix="/api/news", tags=["News"])
app.include_router(attribution_router, prefix="/api/attribution", tags=["Attribution"])
app.include_router(macro_router, prefix="/api/macro", tags=["Macro"])
app.include_router(scan_router, prefix="/api/scan", tags=["Scan"])
app.include_router(data_quality_router, prefix="/api/data-quality", tags=["Data Quality"])
app.include_router(llm_router, prefix="/api/llm", tags=["LLM"])
app.include_router(config_router, prefix="/api/config", tags=["Config"])
app.include_router(health_summary_router, prefix="/api/health/summary", tags=["Health Summary"])
app.include_router(regime_router, prefix="/api/regime", tags=["Regime"])


# ── WebSocket tick stream (legacy frontend) ──
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    log.info("ws.connected", active=len(ws_manager.active_connections))
    try:
        while True:
            await websocket.send_json(_realtime_snapshot())
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                await _handle_ws_command(websocket, message)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        log.info("ws.disconnected", active=len(ws_manager.active_connections))


def _realtime_snapshot() -> dict:
    strategy_status = None
    if getattr(app.state, "red_green_runtime", None):
        strategy_status = app.state.red_green_runtime.get_status()
    return {
        "type": "tick",
        "timestamp": datetime.now(UTC).isoformat(),
        "data": {
            "server_time": datetime.now(UTC).isoformat(),
            "connection": "connected",
            "strategy": strategy_status,
        },
    }


async def _handle_ws_command(websocket: WebSocket, message: str) -> None:
    try:
        cmd = json.loads(message)
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "message": "Invalid JSON"})
        return

    action = cmd.get("action")
    if action == "subscribe":
        symbols = cmd.get("symbols", [])
        await websocket.send_json({"type": "subscribed", "symbols": symbols})
    elif action == "ping":
        await websocket.send_json({"type": "pong", "timestamp": datetime.now(UTC).isoformat()})


# ── Static legacy frontend ──
app.mount("/css", StaticFiles(directory=ROOT_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=ROOT_DIR / "js"), name="js")
if (ROOT_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=ROOT_DIR / "assets"), name="assets")


@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(ROOT_DIR / "index.html")


@app.get("/api/health")
async def health_check() -> dict:
    s = get_settings()
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "runtime_mode": s.runtime_mode.value,
        "app_env": s.app_env.value,
        "mt5_connected": getattr(app.state, "mt5", None) is not None,
        "red_green_runtime": getattr(app.state, "red_green_runtime", None) is not None,
        "llm_providers": s.configured_llm_providers,
        "version": "0.1.0",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
