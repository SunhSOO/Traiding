"""Trading API Routes"""
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class OrderRequest(BaseModel):
    symbol: str
    type: str  # BUY or SELL
    volume: float
    price: Optional[float] = 0
    sl: Optional[float] = 0
    tp: Optional[float] = 0
    comment: Optional[str] = ""


class CloseRequest(BaseModel):
    ticket: int


@router.post("/order")
async def place_order(order: OrderRequest, request: Request):
    mt5 = getattr(request.app.state, 'mt5', None)
    if mt5 and mt5.connected:
        return mt5.place_order(
            symbol=order.symbol, order_type=order.type,
            volume=order.volume, price=order.price,
            sl=order.sl, tp=order.tp, comment=order.comment
        )
    return {"success": False, "error": "MT5 not connected (demo mode)"}


@router.post("/close")
async def close_position(req: CloseRequest, request: Request):
    mt5 = getattr(request.app.state, 'mt5', None)
    if mt5 and mt5.connected:
        return mt5.close_position(req.ticket)
    return {"success": False, "error": "MT5 not connected (demo mode)"}


@router.get("/symbols")
async def get_symbols(request: Request):
    mt5 = getattr(request.app.state, 'mt5', None)
    if mt5 and mt5.connected:
        return mt5.get_symbols()
    return [
        {"name": "EURUSD", "description": "Euro vs US Dollar", "digits": 5, "spread": 4},
        {"name": "XAUUSD", "description": "Gold vs US Dollar", "digits": 2, "spread": 40},
        {"name": "USDJPY", "description": "US Dollar vs Japanese Yen", "digits": 3, "spread": 14},
    ]


@router.get("/tick/{symbol}")
async def get_tick(symbol: str, request: Request):
    mt5 = getattr(request.app.state, 'mt5', None)
    if mt5 and mt5.connected:
        return mt5.get_symbol_tick(symbol)
    return {"symbol": symbol, "bid": 0, "ask": 0, "last": 0}
