"""
backend/api/routes/bot.py

Bot control endpoints — start, stop, status, activity logs.
Validates broker connectivity before allowing bot to start.
"""

import json
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List

from backend.data.database import get_db
from backend.data.models import User, UserConfigModel
from backend.api.deps import get_current_user
from backend.services.bot_service import bot_service
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["bot"])

# Deriv / synthetic symbols that require the Deriv broker
DERIV_SYMBOLS = {
    "Volatility 10 Index", "Volatility 25 Index", "Volatility 50 Index",
    "Volatility 75 Index", "Volatility 100 Index",
    "Boom 300 Index", "Boom 500 Index", "Boom 1000 Index",
    "Crash 300 Index", "Crash 500 Index", "Crash 1000 Index",
    "Step Index", "Range Break 100 Index", "Range Break 200 Index",
}


def _needs_standard_broker(symbols: list) -> bool:
    """Check if any symbol requires a standard MT5 broker (forex, metals, indices)."""
    return any(s not in DERIV_SYMBOLS for s in symbols)


def _needs_deriv_broker(symbols: list) -> bool:
    """Check if any symbol requires a Deriv broker (synthetics)."""
    return any(s in DERIV_SYMBOLS for s in symbols)


class StartBotRequest(BaseModel):
    symbols: Optional[List[str]] = None
    scan_interval: int = 60


@router.post("/bot/start")
async def start_bot(
    req: StartBotRequest = StartBotRequest(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Start the trading bot for the authenticated user.
    1. Loads symbols from user config if not provided
    2. Validates that required brokers are connected for selected symbols
    3. Starts the bot scanning loop
    """
    # 1. Load symbols from user config if not provided in request
    symbols = req.symbols
    if not symbols:
        result = await db.execute(
            select(UserConfigModel).where(UserConfigModel.user_id == current_user.id)
        )
        user_config = result.scalar_one_or_none()
        if user_config:
            config_data = json.loads(user_config.config_json) if user_config.config_json else {}
            symbols = config_data.get("watched_symbols", config_data.get("symbols", []))

    # Fall back to defaults if still empty
    if not symbols:
        symbols = ["XAUUSD", "EURUSD", "GBPUSD"]

    # 2. Validate broker connectivity for selected symbols
    missing_brokers = []

    if _needs_standard_broker(symbols):
        if not current_user.mt5_account:
            standard_needed = [s for s in symbols if s not in DERIV_SYMBOLS]
            missing_brokers.append(
                f"Standard MT5 broker not configured — required for: {', '.join(standard_needed)}"
            )

    if _needs_deriv_broker(symbols):
        if not getattr(current_user, 'deriv_mt5_account', None):
            deriv_needed = [s for s in symbols if s in DERIV_SYMBOLS]
            missing_brokers.append(
                f"Deriv MT5 broker not configured — required for: {', '.join(deriv_needed)}"
            )

    if missing_brokers:
        bot_service.log_system_event(
            f"Bot start blocked: {'; '.join(missing_brokers)}",
            level="ERROR", category="BOT"
        )
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Broker configuration required",
                "missing_brokers": missing_brokers,
                "symbols": symbols,
            }
        )

    # 3. Start the bot
    bot_service.log_system_event(
        f"Bot starting with {len(symbols)} symbols: {', '.join(symbols)}",
        category="BOT"
    )
    result = await bot_service.start(
        user_id=current_user.id,
        symbols=symbols,
        scan_interval=req.scan_interval,
    )
    return result


@router.post("/bot/stop")
async def stop_bot(
    current_user: User = Depends(get_current_user),
):
    """Stop the trading bot."""
    result = await bot_service.stop()
    return result


@router.get("/bot/status")
async def get_bot_status(
    current_user: User = Depends(get_current_user),
):
    """Get current bot status."""
    return bot_service.get_status()


@router.get("/bot/logs")
async def get_bot_logs(
    limit: int = Query(50, le=200),
    current_user: User = Depends(get_current_user),
):
    """Get recent bot activity log entries."""
    return bot_service.get_logs(limit=limit)
