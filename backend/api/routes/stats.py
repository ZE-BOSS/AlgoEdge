"""
backend/api/routes/stats.py

Performance analytics API.
Source: TradingBot_MasterPlan-2.md Section 6
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics.metrics import compute_portfolio_stats
from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import PerformanceStats, Trade, User
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
async def get_user_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregate performance stats for the authenticated user."""
    logger.info(f"Computing stats for {current_user.email}")
    # DB Cache check removed: Always compute live from MT5 for accuracy.

    # 1. Try to get deals from MT5 history first
    try:
        from backend.mt5.order_manager import OrderManager
        deals = await OrderManager.get_closed_positions_since(0)
        
        if deals:
            # We got MT5 data! Sort chronologically
            deals.sort(key=lambda x: x["time"])
            
            # Fetch MT5 live balance
            import MetaTrader5 as mt5
            account_info = mt5.account_info()
            live_balance = account_info.balance if account_info else 10000.0
            
            total_mt5_pnl = sum((d["profit"] + d["commission"] + d["swap"]) for d in deals)
            initial_balance = live_balance - total_mt5_pnl
            if initial_balance <= 0:
                initial_balance = 10000.0
                
            trade_dicts = [{
                "pnl": d["profit"] + d["commission"] + d["swap"],
                "exit_reason": "TP" if (d["profit"] > 0) else "SL",
                "be_applied": False,
            } for d in deals]
            
            return compute_portfolio_stats(trade_dicts, initial_balance=initial_balance)
    except Exception as e:
        logger.warning(f"Could not compute stats from MT5: {e}. Falling back to DB.")

    # 2. Compute live from DB trades (Fallback)
    trades_result = await db.execute(
        select(Trade).where(Trade.user_id == current_user.id, Trade.status == "CLOSED").order_by(Trade.entry_time.asc())
    )
    trades = trades_result.scalars().all()
    
    initial_balance = 10000.0
    if trades and trades[0].balance_before is not None and trades[0].balance_before > 0:
        initial_balance = trades[0].balance_before
    elif trades and trades[-1].balance_after is not None and trades[-1].balance_after > 0:
        total_pnl = sum(t.pnl for t in trades if t.pnl is not None)
        initial_balance = trades[-1].balance_after - total_pnl
        if initial_balance <= 0:
            initial_balance = 10000.0
            
    trade_dicts = [{
        "pnl": t.pnl,
        "exit_reason": t.exit_reason,
        "be_applied": False,
    } for t in trades]

    return compute_portfolio_stats(trade_dicts, initial_balance=initial_balance)
