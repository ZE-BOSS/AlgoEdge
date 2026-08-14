"""
backend/api/routes/dashboard.py
Consolidated dashboard endpoint.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.api.routes.bot import get_bot_status
from backend.api.routes.broker import get_broker_status
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
        broker_data = await get_broker_status(current_user, db)
    except Exception:
        broker_data = {}
        
    try:
        bot_result = await get_bot_status(current_user)
    except Exception:
        bot_result = {}
        
    try:
        from backend.services.bot_service import bot_service
        if getattr(bot_service, "prop_firm_validator", None) and getattr(bot_service.prop_firm_validator, "enabled", False):
            pv = bot_service.prop_firm_validator
            
            # Use live MT5 account info for accuracy
            try:
                import MetaTrader5 as mt5
                from datetime import datetime
                acc = mt5.account_info()
                if acc:
                    # Accurately calculate active trading days and net deposits directly from MT5 history
                    deals = mt5.history_deals_get(datetime(2020, 1, 1), datetime.utcnow())
                    net_deposits = 0.0
                    if deals:
                        active_days = set()
                        for d in deals:
                            # Balance operations (2=BALANCE, 3=CREDIT, etc)
                            if getattr(d, 'type', 0) >= 2:
                                net_deposits += getattr(d, 'profit', 0.0)
                            
                            # Only count executed trades (volume > 0 and has a symbol) for active days
                            if getattr(d, 'symbol', '') != '' and getattr(d, 'volume', 0.0) > 0:
                                trade_date = datetime.fromtimestamp(d.time).date()
                                active_days.add(trade_date)
                        pv.active_trading_days = len(active_days)
                        
                    pv.update_equity_balance(acc.equity, acc.balance, datetime.utcnow(), net_deposits=net_deposits)
                    pv.save_state()
            except Exception as e:
                logger.error(f"Failed to sync MT5 Prop Firm state: {e}")

            pf_state = {
                "high_water_mark": pv.high_water_mark,
                "eod_baseline": pv.eod_baseline,
                "daily_profit": pv.daily_profit,
                "total_profit": pv.total_profit,
                "active_trading_days": pv.active_trading_days,
                "is_paused": pv.is_breached,
                "pause_reason": pv.breach_reason,
            }
        else:
            pf_state = None
    except Exception:
        pf_state = None
    
    return {
        "stats": stats_data,
        "config": config_data,
        "positions": positions_data,
        "broker": broker_data,
        "bot": bot_result,
        "prop_firm_status": pf_state,
        "mt5_sync": mt5_sync_result
    }


@router.post("/prop-firm/reset-breach")
async def reset_prop_firm_breach(
    current_user: User = Depends(get_current_user),
):
    """
    Manually clear a prop firm drawdown breach.
    Allows the user to resume trading after reviewing the breach reason.
    Sends a Telegram notification on reset.
    """
    try:
        from backend.services.bot_service import bot_service
        pv = getattr(bot_service, "prop_firm_validator", None)
        if pv and pv.enabled:
            if not pv.is_breached:
                return {"status": "ok", "message": "No active breach to reset."}
            prev_reason = pv.breach_reason
            pv.reset_breach()
            return {"status": "ok", "message": f"Breach cleared. Previous reason: {prev_reason}"}
        return {"status": "ok", "message": "Prop Firm mode is not active."}
    except Exception as e:
        logger.error(f"Failed to reset prop firm breach: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/circuit-breaker/reset")
async def reset_circuit_breaker(
    current_user: User = Depends(get_current_user),
):
    """
    Manually reset the circuit breaker (clears all streaks and resumes trading).
    Equivalent to calling manual_resume() on the live CircuitBreaker instance.
    """
    try:
        from backend.services.bot_service import bot_service
        cb = getattr(bot_service, "circuit_breaker", None)
        if cb:
            cb.manual_resume()
            return {"status": "ok", "message": "Circuit breaker reset. Trading resumed."}
        return {"status": "ok", "message": "Circuit breaker not initialized (bot not running)."}
    except Exception as e:
        logger.error(f"Failed to reset circuit breaker: {e}")
        return {"status": "error", "message": str(e)}
