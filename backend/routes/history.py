"""History API Routes"""
from fastapi import APIRouter, Request, Query

router = APIRouter()


@router.get("/deals")
async def get_deal_history(request: Request, days: int = Query(default=30, ge=1, le=365)):
    mt5 = getattr(request.app.state, 'mt5', None)
    if mt5 and mt5.connected:
        return mt5.get_history(days=days)
    return []
