"""
backend/main.py

FastAPI application entry point.
All routes registered, DB initialized, CORS configured for remote frontend access.
"""

import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware

# Import API route modules
from backend.api.routes import (
    admin,
    auth,
    backtest,
    bot,
    broker,
    charts,
    config,
    dashboard,
    llm,
    mt5_test,
    push,
    signals,
    stats,
    trades,
)
from backend.data.database import close_db, init_db
from backend.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Starting AlgoEdge Backend...")

    # 1. Init Database (PostgreSQL on Railway)
    await init_db()
    logger.info("Database initialized")

    # Clean up ghost manual trades on boot
    try:
        from sqlalchemy import select

        from backend.data.database import async_session
        from backend.data.models import Trade, TradePosition
        async with async_session() as session:
            # 1. Clean Ghosts
            result = await session.execute(select(Trade).where(Trade.status == "OPEN", Trade.strategy_id == "MANUAL"))
            ghosts = result.scalars().all()
            for t in ghosts:
                pos_res = await session.execute(select(TradePosition).where(TradePosition.parent_trade_id == t.id))
                for p in pos_res.scalars().all(): await session.delete(p)
                await session.delete(t)
            
            # 2. Repair Corrupted Initial SLs for open trades
            result2 = await session.execute(select(Trade).where(Trade.status == "OPEN"))
            open_trades = result2.scalars().all()
            repaired = 0
            for t in open_trades:
                tp1_res = await session.execute(select(TradePosition).where(TradePosition.parent_trade_id == t.id, TradePosition.tp_level == 1))
                tp1 = tp1_res.scalar_one_or_none()
                if tp1 and tp1.take_profit:
                    # Risk = TP1 - Entry, so SL = Entry - Risk (for buy) => 2*Entry - TP1
                    t.stop_loss = 2 * tp1.entry_price - tp1.take_profit
                    repaired += 1
            
            # Dump DB state
            try:
                dump_lines = ["=== OPEN TRADES ==="]
                for t in open_trades:
                    dump_lines.append(f"Trade ID: {t.id} | Strategy: {t.strategy_id} | Symbol: {t.symbol} | Dir: {t.direction} | Vol: {t.volume} | Entry: {t.entry_price}")
                    pos_res = await session.execute(select(TradePosition).where(TradePosition.parent_trade_id == t.id))
                    for p in pos_res.scalars().all():
                        dump_lines.append(f"   -> Sub-Pos: TP{p.tp_level} | Ticket: {p.mt5_ticket} | Vol: {p.volume} | SL: {p.stop_loss} | TP: {p.take_profit}")
                with open("db_state.txt", "w") as f:
                    f.write("\n".join(dump_lines))
            except Exception as e:
                logger.error(f"Dump failed: {e}")
                
            await session.commit()
            if ghosts: logger.info(f"Cleaned {len(ghosts)} ghost manual trades.")
            if repaired: logger.info(f"Repaired Initial SL for {repaired} live trades.")
    except Exception as e:
        logger.error(f"Ghost cleanup / SL repair failed: {e}")

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
            from backend.brokers.factory import broker_factory
            broker = broker_factory.get_broker("MT5")
            await broker.connect()
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


from backend.utils.global_error_handler import setup_global_error_handler
setup_global_error_handler()

app = FastAPI(
    title="AlgoEdge Trading Bot API",
    version="1.0.0",
    description="Smart Money Concepts algorithmic trading backend",
    lifespan=lifespan,
)

# CORS — allow any frontend (Vercel, Netlify, etc.) to reach the local backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in [
        os.getenv("FRONTEND_URL", ""),
        "http://52.201.102.37",
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ] if o],
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
app.include_router(llm.router)
app.include_router(push.router)
app.include_router(signals.router)
app.include_router(bot.router)
app.include_router(broker.router)
app.include_router(mt5_test.router)
app.include_router(dashboard.router)


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
async def websocket_endpoint(websocket: WebSocket, user_id: str, token: str = None):
    """WebSocket endpoint for live dashboard updates."""
    from backend.api.websocket import websocket_handler
    await websocket_handler(websocket, user_id, token)
