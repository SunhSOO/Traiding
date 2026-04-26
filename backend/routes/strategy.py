"""Strategy API Routes"""
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class StrategyConfigRequest(BaseModel):
    symbol: str | None = None
    timeframe: str | None = None
    volume: float | None = None
    magic: int | None = None
    dry_run: bool | None = None
    live_enabled: bool | None = None
    k: int | None = None
    d: int | None = None
    d1_input: int | None = None
    mult: float | None = None
    short_period: int | None = None
    mid_period: int | None = None
    long_period: int | None = None
    atr_periods: int | None = None
    atr_multiplier: float | None = None
    tp1_mult: float | None = None
    max_spread_points: int | None = None

    def clean(self) -> dict[str, Any]:
        return {key: value for key, value in self.model_dump().items() if value is not None}


def get_manager(request: Request):
    return getattr(request.app.state, "strategy_manager", None)


@router.get("/list")
async def list_strategies(request: Request):
    manager = get_manager(request)
    if not manager:
        return []
    status = manager.get_status()
    return [{
        "id": status["id"],
        "name": status["name"],
        "status": status["status"],
        "symbol": status["config"]["symbol"],
        "timeframe": status["config"]["timeframe"],
        "mode": status["mode"],
    }]


@router.get("/red-green/status")
async def red_green_status(request: Request):
    manager = get_manager(request)
    if not manager:
        return {"success": False, "error": "Strategy manager unavailable"}
    return {"success": True, "status": manager.get_status()}


@router.patch("/red-green/config")
async def update_red_green_config(payload: StrategyConfigRequest, request: Request):
    manager = get_manager(request)
    if not manager:
        return {"success": False, "error": "Strategy manager unavailable"}
    return manager.update_config(payload.clean())


@router.post("/red-green/start")
async def start_red_green(request: Request):
    manager = get_manager(request)
    if not manager:
        return {"success": False, "error": "Strategy manager unavailable"}
    return await manager.start()


@router.post("/red-green/stop")
async def stop_red_green(request: Request):
    manager = get_manager(request)
    if not manager:
        return {"success": False, "error": "Strategy manager unavailable"}
    return await manager.stop()


@router.post("/red-green/run-once")
async def run_red_green_once(request: Request):
    manager = get_manager(request)
    if not manager:
        return {"success": False, "error": "Strategy manager unavailable"}
    return await manager.run_once()


@router.post("/{strategy_id}/start")
async def start_strategy(strategy_id: str, request: Request):
    if strategy_id in {"red-green-main", "1"}:
        return await start_red_green(request)
    return {"success": False, "error": f"Unknown strategy: {strategy_id}"}


@router.post("/{strategy_id}/stop")
async def stop_strategy(strategy_id: str, request: Request):
    if strategy_id in {"red-green-main", "1"}:
        return await stop_red_green(request)
    return {"success": False, "error": f"Unknown strategy: {strategy_id}"}
