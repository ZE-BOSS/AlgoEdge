"""
backend/api/routes/dashboard.py
Consolidated dashboard endpoint.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.api.routes.bot import get_bot_status
from backend.api.routes.broker import get_broker_status
from backend.api.routes.compounding import get_compounding_state
from backend.api.routes.config import get_user_config
from backend.api.routes.stats import get_user_stats
from backend.api.routes.trades import get_open_positions
from backend.data.database import get_db
from backend.data.models import User

router = APIRouter(prefix="/api", tags=["dashboard"])

@router.get("/dashboard")
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return consolidated data for the dashboard."""
    # Execute sequentially to avoid shared session issues
    try:
        stats_data = await get_user_stats(current_user, db)
    except Exception:
        stats_data = {}
        
    try:
        config_data = await get_user_config(current_user, db)
    except Exception:
        config_data = {}
        
    try:
        positions_data = await get_open_positions(current_user, db)
    except Exception:
        positions_data = []
        
    try:
        compounding_data = await get_compounding_state(current_user, db)
    except Exception:
        compounding_data = {}
        
    try:
        broker_data = await get_broker_status(current_user, db)
    except Exception:
        broker_data = {}
        
    try:
        bot_result = await get_bot_status(current_user)
    except Exception:
        bot_result = {}
        
    try:
        from backend.services.bot_service import bot_service
        if getattr(bot_service, "prop_firm_validator", None):
            pf_state = {
                "high_water_mark": bot_service.prop_firm_validator.high_water_mark,
                "eod_baseline": bot_service.prop_firm_validator.eod_baseline,
                "daily_profit": bot_service.prop_firm_validator.daily_profit,
                "total_profit": bot_service.prop_firm_validator.total_profit,
                "active_trading_days": bot_service.prop_firm_validator.active_trading_days,
                "is_paused": bot_service.prop_firm_validator.is_paused,
                "pause_reason": bot_service.prop_firm_validator.pause_reason,
            }
        else:
            pf_state = None
    except Exception:
        pf_state = None
    
    return {
        "stats": stats_data,
        "config": config_data,
        "positions": positions_data,
        "compounding": compounding_data,
        "broker": broker_data,
        "bot": bot_result,
        "prop_firm_status": pf_state
    }
