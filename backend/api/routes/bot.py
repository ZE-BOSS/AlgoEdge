"""
backend/api/routes/bot.py

Bot control endpoints — start, stop, status, activity logs.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional, List

from backend.data.models import User
from backend.api.deps import get_current_user
from backend.services.bot_service import bot_service
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["bot"])


class StartBotRequest(BaseModel):
    symbols: Optional[List[str]] = None
    scan_interval: int = 60


@router.post("/bot/start")
async def start_bot(
    req: StartBotRequest = StartBotRequest(),
    current_user: User = Depends(get_current_user),
):
    """Start the trading bot for the authenticated user."""
    result = await bot_service.start(
        user_id=current_user.id,
        symbols=req.symbols,
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
