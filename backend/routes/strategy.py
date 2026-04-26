"""Strategy API Routes"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/list")
async def list_strategies():
    """List all configured strategies"""
    return [
        {"id": 1, "name": "Trend Follower", "status": "running", "symbol": "EURUSD", "timeframe": "H1"},
        {"id": 2, "name": "Gold Scalper", "status": "running", "symbol": "XAUUSD", "timeframe": "M15"},
        {"id": 3, "name": "Mean Reversion", "status": "stopped", "symbol": "USDJPY", "timeframe": "H4"},
    ]


@router.post("/{strategy_id}/start")
async def start_strategy(strategy_id: int):
    return {"success": True, "id": strategy_id, "status": "running"}


@router.post("/{strategy_id}/stop")
async def stop_strategy(strategy_id: int):
    return {"success": True, "id": strategy_id, "status": "stopped"}
