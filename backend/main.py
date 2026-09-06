"""
backend/main.py

FastAPI application entry point.
All routes registered, DB initialized, CORS configured for remote frontend access.
"""

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware

# Import API route modules
from backend.api.routes import (
    admin,
    analysis,
    auth,
    backtest,
    bot,
    broker,
    charts,
    config,
    dashboard,
    llm,
    logs,
    fundamentals,
    mt5_test,
    push,
    signals,
    stats,
    strategy_factory,
    system,
    trades,
)
from backend.data.database import close_db, init_db
from backend.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Starting AlgoEdge Backend...")

    # [Phase 13 section G] Attach the log hub to the WebSocket manager. The
    # loguru sink itself is installed at import time in utils/logger.py, so
    # records are already being captured into the ring buffer by now; this is
    # what starts the pump that pushes them to connected clients. It has to
    # happen here rather than at import because creating the pump task needs a
    # running event loop.
    try:
        from backend.api.websocket import manager as ws_manager
        from backend.services.log_stream import log_hub
        log_hub.attach(ws_manager)
        logger.info("Log stream attached to WebSocket manager")
    except Exception as e:
        logger.warning(f"Log stream not attached: {e}")

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

        # A backtest cannot survive a process restart, but its Redis status can
        # (1h TTL). Without this, a client polling after a restart sees a run
        # still "running", parks on "Stop Backtest", and never starts another.
        try:
            from backend.api.routes.backtest import reconcile_orphaned_runs
            await reconcile_orphaned_runs()
        except Exception as e:
            logger.warning(f"Orphaned-run reconciliation skipped: {e}")
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

    # 4. Load Telegram credentials into the notification service.
    #
    # These used to be loaded ONLY from inside the bot's scan loop, which meant
    # the process had no token at all until the bot was started — so every alert
    # raised before that (startup errors, prop-firm breaches, manual test sends)
    # was dropped silently by the "not configured" guard. Loading here makes
    # notifications work independently of whether the bot is running.
    tg_ok = False
    try:
        from backend.services.telegram import load_telegram_config_any_user
        tg_ok = await load_telegram_config_any_user()
        logger.info(
            "Telegram credentials loaded" if tg_ok
            else "Telegram not configured — notifications disabled"
        )
    except Exception as e:
        logger.warning(f"Telegram config load failed: {e}")

    # Log startup events to the activity log for frontend visibility
    from backend.services.bot_service import bot_service
    bot_service.log_system_event("AlgoEdge Backend started", category="SYSTEM")
    bot_service.log_system_event(f"Database: connected | Redis: {'connected' if redis_ok else 'offline'} | MT5: {'connected' if mt5_ok else 'mock mode'}", category="SYSTEM")
    bot_service.log_system_event(
        f"Telegram: {'configured' if tg_ok else 'NOT configured — no alerts will be sent'}",
        "INFO" if tg_ok else "WARN", category="SYSTEM",
    )

    yield

    # Shutdown
    logger.info("Shutting down AlgoEdge Backend...")

    # Disconnect the broker FIRST. Its disconnect() cancels the MT5
    # auto-reconnect daemon, which is an infinite `while True` task. Without
    # this, uvicorn's graceful shutdown waits on that task forever and the
    # process hangs on "Waiting for background tasks to complete" — wedging
    # every --reload cycle in dev and blocking `pm2 restart` in production
    # until pm2's kill_timeout force-kills it.
    try:
        from backend.brokers.factory import BrokerFactory
        # Read the singleton directly rather than calling get_broker(), which
        # would CONSTRUCT a broker during shutdown if none was ever created.
        active_broker = BrokerFactory._broker_instance
        if active_broker is not None:
            await active_broker.disconnect()
    except Exception as e:
        logger.warning(f"Broker disconnect during shutdown failed (continuing): {e}")

    # The log pump is the other infinite background task; same hang, same fix.
    try:
        from backend.services.log_stream import log_hub
        stop = getattr(log_hub, "stop", None)
        if callable(stop):
            res = stop()
            if asyncio.iscoroutine(res):
                await res
    except Exception as e:
        logger.warning(f"Log hub stop during shutdown failed (continuing): {e}")

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
app.include_router(logs.router)
app.include_router(analysis.router)
app.include_router(fundamentals.router)
app.include_router(push.router)
app.include_router(signals.router)
app.include_router(bot.router)
app.include_router(broker.router)
app.include_router(mt5_test.router)
app.include_router(dashboard.router)
app.include_router(strategy_factory.router)  # [Phase 14 Stream 3]
app.include_router(system.router)  # Telegram status/test, live account, state reset


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
