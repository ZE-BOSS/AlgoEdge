"""
backend/main.py

FastAPI application entry point.
All routes registered, DB initialized, CORS configured for remote frontend access.
"""

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import time

from backend.config import settings
from backend.utils.logger import get_logger
from backend.data.database import init_db, close_db
from backend.api.websocket import manager as ws_manager

# Import API route modules
from backend.api.routes import trades, stats, admin, backtest, config, charts, compounding, llm, push, auth, signals, bot

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Starting AlgoEdge Backend...")

    # 1. Init Database (PostgreSQL on Railway)
    await init_db()
    logger.info("Database initialized")

    # 2. Connect Redis (optional — graceful skip if unavailable)
    redis_ok = False
    try:
        from backend.data.redis_client import redis_client
        await redis_client.connect()
        redis_ok = True
        logger.info("Redis connected")
    except Exception as e:
        logger.warning(f"Redis not available — skipping: {e}")

    # 3. Connect MT5 (only on Windows)
    mt5_ok = False
    if os.name == "nt":
        try:
            from backend.mt5.bridge import bridge
            await bridge.connect()
            mt5_ok = True
            logger.info("MT5 connected")
        except Exception as e:
            logger.warning(f"MT5 not available — running in mock mode: {e}")
    else:
        logger.info("MT5 skipped (not Windows) — using mock data")

    # Log startup events to the activity log for frontend visibility
    from backend.services.bot_service import bot_service
    bot_service.log_system_event("AlgoEdge Backend started", category="SYSTEM")
    bot_service.log_system_event(f"Database: connected | Redis: {'connected' if redis_ok else 'offline'} | MT5: {'connected' if mt5_ok else 'mock mode'}", category="SYSTEM")

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
app.include_router(bot.router)


# ── Request Logging Middleware ───────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every API request/response to the terminal for transparency."""
    start = time.time()
    method = request.method
    path = request.url.path

    # Skip noisy health/polling endpoints from detailed logs
    is_polling = path in ("/api/health", "/api/bot/status", "/api/bot/logs")

    if not is_polling:
        logger.info(f"→ {method} {path}")

    response = await call_next(request)
    elapsed = (time.time() - start) * 1000  # ms

    if not is_polling:
        status_icon = "✓" if response.status_code < 400 else "✗"
        logger.info(f"{status_icon} {method} {path} → {response.status_code} ({elapsed:.0f}ms)")
    elif response.status_code >= 400:
        logger.warning(f"✗ {method} {path} → {response.status_code} ({elapsed:.0f}ms)")

    return response


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
