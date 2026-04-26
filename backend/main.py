"""
SUPERRICH - FastAPI Backend for MT5 System Trading
Main application entry point
"""
import asyncio
import json
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Import routes
from routes.account import router as account_router
from routes.trading import router as trading_router
from routes.history import router as history_router
from routes.strategy import router as strategy_router
from websocket_manager import ConnectionManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    logger.info("🚀 SUPERRICH Trading Server starting...")
    # Initialize MT5 connection on startup
    try:
        from mt5_bridge import MT5Bridge
        bridge = MT5Bridge()
        if bridge.connect():
            logger.info("✅ MT5 connection established")
            app.state.mt5 = bridge
        else:
            logger.warning("⚠️ MT5 not available - running in demo mode")
            app.state.mt5 = None
    except Exception as e:
        logger.warning(f"⚠️ MT5 initialization failed: {e} - running in demo mode")
        app.state.mt5 = None
    
    yield
    
    # Cleanup on shutdown
    if hasattr(app.state, 'mt5') and app.state.mt5:
        app.state.mt5.disconnect()
        logger.info("MT5 disconnected")
    logger.info("Server shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="SUPERRICH Trading API",
    description="MT5 System Trading Dashboard Backend",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket manager
ws_manager = ConnectionManager()

# Register API routes
app.include_router(account_router, prefix="/api/account", tags=["Account"])
app.include_router(trading_router, prefix="/api/trading", tags=["Trading"])
app.include_router(history_router, prefix="/api/history", tags=["History"])
app.include_router(strategy_router, prefix="/api/strategy", tags=["Strategy"])


# ── WebSocket endpoint for real-time data ──
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    logger.info(f"WebSocket client connected. Active: {len(ws_manager.active_connections)}")
    
    try:
        while True:
            # Send real-time data every second
            data = get_realtime_data()
            await websocket.send_json(data)
            
            # Also listen for commands from client
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                await handle_ws_command(websocket, message)
            except asyncio.TimeoutError:
                pass
                
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        logger.info(f"WebSocket client disconnected. Active: {len(ws_manager.active_connections)}")


def get_realtime_data():
    """Get real-time data snapshot from MT5 or demo data"""
    return {
        "type": "tick",
        "timestamp": datetime.utcnow().isoformat(),
        "data": {
            "server_time": datetime.utcnow().isoformat(),
            "connection": "connected"
        }
    }


async def handle_ws_command(websocket: WebSocket, message: str):
    """Handle incoming WebSocket commands"""
    try:
        cmd = json.loads(message)
        action = cmd.get("action")
        
        if action == "subscribe":
            symbols = cmd.get("symbols", [])
            logger.info(f"Client subscribed to: {symbols}")
            await websocket.send_json({"type": "subscribed", "symbols": symbols})
        
        elif action == "ping":
            await websocket.send_json({"type": "pong", "timestamp": datetime.utcnow().isoformat()})
    
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "message": "Invalid JSON"})


# ── Serve static files ──
# Mount static files last so API routes take priority
app.mount("/css", StaticFiles(directory="../css"), name="css")
app.mount("/js", StaticFiles(directory="../js"), name="js")
app.mount("/assets", StaticFiles(directory="../assets"), name="assets")


@app.get("/")
async def serve_index():
    """Serve the main HTML file"""
    return FileResponse("../index.html")


# ── Health check ──
@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "mt5_connected": hasattr(app.state, 'mt5') and app.state.mt5 is not None,
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
