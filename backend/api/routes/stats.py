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

    # Sharpe/Sortino need the same normaliser the LIVE sizer is actually using
    # (RiskParams.sizing_basis, resolved in bot_service via
    # resolve_sizing_base_balance). Under BALANCE/EQUITY dollar risk scales with
    # the account, so a fixed-capital denominator deflates both ratios. Falls
    # back to STATIC — the schema default — if the user has no saved config.
    sizing_basis = "STATIC"
    try:
        import json as _json

        from backend.core.config_schema import UserConfigV2
        from backend.data.models import UserConfigModel as _UserConfigModel

        _cfg_row = (
            await db.execute(
                select(_UserConfigModel).where(_UserConfigModel.user_id == current_user.id)
            )
        ).scalar_one_or_none()
        if _cfg_row and _cfg_row.config_json:
            _parsed = UserConfigV2.from_dict(_json.loads(_cfg_row.config_json))
            sizing_basis = getattr(_parsed.risk, "sizing_basis", "STATIC") or "STATIC"
    except Exception as e:
        logger.warning(f"Could not resolve sizing_basis for stats, defaulting to STATIC: {e}")
    # DB Cache check removed: Always compute live from MT5 for accuracy.

    # 1. Try to get deals from MT5 history first
    try:
        from backend.mt5.order_manager import OrderManager
        deals = await OrderManager.get_closed_positions_since(0)
        
        if deals:
            # Fetch balance operations to calculate net deposits
            balance_ops = await OrderManager.get_balance_operations_since(0)
            net_deposits = sum(op.get("profit", 0.0) for op in balance_ops)
            
            # We got MT5 data! Sort chronologically
            deals.sort(key=lambda x: x["time"])
            
            # Fetch MT5 live balance
            import MetaTrader5 as mt5
            account_info = mt5.account_info()
            live_balance = account_info.balance if account_info else 10000.0
            
            total_mt5_pnl = sum((d["profit"] + d["commission"] + d["swap"]) for d in deals)
            # Initial Balance = Current Balance - Net Deposits - PnL
            initial_balance = live_balance - net_deposits - total_mt5_pnl
            if initial_balance <= 0:
                initial_balance = 10000.0
                
            # Fetch local DB trades to get the accurate exit_reason (e.g., TP1, TP2) instead of just "TP"
            db_trades_result = await db.execute(
                select(Trade).where(Trade.user_id == current_user.id, Trade.status == "CLOSED")
            )
            db_trades = {str(t.position_id): t.exit_reason for t in db_trades_result.scalars().all() if t.position_id}
                
            trade_dicts = []
            for d in deals:
                p_id = str(d.get("position_id", ""))
                mapped_reason = db_trades.get(p_id)
                if not mapped_reason:
                    mapped_reason = "TP" if (d["profit"] > 0) else "SL"
                    
                trade_dicts.append({
                    "pnl": d["profit"] + d["commission"] + d["swap"],
                    "exit_reason": mapped_reason,
                    "be_applied": False,
                })
            
            return compute_portfolio_stats(trade_dicts, initial_balance=initial_balance, sizing_basis=sizing_basis)
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

    return compute_portfolio_stats(trade_dicts, initial_balance=initial_balance, sizing_basis=sizing_basis)
