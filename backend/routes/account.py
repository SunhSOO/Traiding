"""Account API Routes"""
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/info")
async def get_account_info(request: Request):
    mt5 = getattr(request.app.state, 'mt5', None)
    if mt5 and mt5.connected:
        return mt5.get_account_info()
    return {
        "login": 12345678, "name": "Demo User", "server": "MetaQuotes-Demo",
        "balance": 52847.63, "equity": 53412.18, "margin": 4280.00,
        "free_margin": 49132.18, "margin_level": 1247.48, "leverage": 100,
        "currency": "USD", "profit": 564.55
    }


@router.get("/positions")
async def get_positions(request: Request):
    mt5 = getattr(request.app.state, 'mt5', None)
    if mt5 and mt5.connected:
        return mt5.get_positions()
    return []


@router.get("/orders")
async def get_orders(request: Request):
    mt5 = getattr(request.app.state, 'mt5', None)
    if mt5 and mt5.connected:
        return mt5.get_orders()
    return []
