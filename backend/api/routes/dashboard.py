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
from backend.services.sync_service import sync_mt5_history
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"])

@router.get("/dashboard")
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return consolidated data for the dashboard."""
    # 1. First sync with MT5 to ensure DB is 100% accurate
    try:
        mt5_sync_result = await sync_mt5_history(current_user, db, hours_back=72)
    except Exception as e:
        logger.error(f"MT5 sync failed in dashboard: {e}")
        mt5_sync_result = {"status": "error", "reason": str(e)}

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
            pv = bot_service.prop_firm_validator
            
            # Use live MT5 account info for accuracy
            try:
                import MetaTrader5 as mt5
                acc = mt5.account_info()
                if acc:
                    pv.update_equity_balance(acc.equity, acc.balance)
            except Exception:
                pass

            pf_state = {
                "high_water_mark": pv.high_water_mark,
                "eod_baseline": pv.eod_baseline,
                "daily_profit": pv.daily_profit,
                "total_profit": pv.total_profit,
                "active_trading_days": pv.active_trading_days,
                "is_paused": pv.is_paused,
                "pause_reason": pv.pause_reason,
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
        "prop_firm_status": pf_state,
        "mt5_sync": mt5_sync_result
    }
