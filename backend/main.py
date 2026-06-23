"""
backend/main.py

FastAPI application entry point.
All routes registered, DB initialized, CORS configured for remote frontend access.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os

from backend.config import settings
from backend.utils.logger import get_logger
from backend.data.database import init_db, close_db
from backend.api.websocket import manager as ws_manager

# Import API route modules
from backend.api.routes import trades, stats, admin, backtest, config, charts, compounding, llm, push, auth, signals

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Starting AlgoEdge Backend...")

    # 1. Init Database (PostgreSQL on Railway)
    await init_db()

    # 2. Connect Redis (optional — graceful skip if unavailable)
    try:
        from backend.data.redis_client import redis_client
        await redis_client.connect()
    except Exception as e:
        logger.warning(f"Redis not available — skipping: {e}")

    # 3. Connect MT5 (only on Windows)
    if os.name == "nt":
        try:
            from backend.mt5.bridge import bridge
            await bridge.connect()
        except Exception as e:
            logger.warning(f"MT5 not available — running in mock mode: {e}")

    yield

    # Shutdown
    logger.info("Shutting down AlgoEdge Backend...")
    try:
        from backend.data.redis_client import redis_client
        await redis_client.disconnect()
    except Exception:
        pass
    await close_db()


app = FastAPI(
    title="AlgoEdge Trading Bot API",
    version="1.0.0",
    description="Smart Money Concepts algorithmic trading backend",
    lifespan=lifespan,
)

# CORS — allow any frontend (Vercel, Netlify, etc.) to reach the local backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*",                                    # Dev: allow all
        os.getenv("FRONTEND_URL", ""),          # Production: specific frontend URL
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register API Routers ─────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(trades.router)
app.include_router(stats.router)
app.include_router(admin.router)
app.include_router(backtest.router)
app.include_router(config.router)
app.include_router(charts.router)
app.include_router(compounding.router)
app.include_router(llm.router)
app.include_router(push.router)
app.include_router(signals.router)


# ── Health Check ─────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "service": "AlgoEdge Backend", "version": "1.0.0"}


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """WebSocket endpoint for live dashboard updates."""
    await ws_manager.connect(websocket, user_id)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, user_id)
