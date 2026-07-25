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
    # Try cached stats first
    result = await db.execute(
        select(PerformanceStats)
        .where(PerformanceStats.user_id == current_user.id)
        .order_by(PerformanceStats.updated_at.desc())
        .limit(1)
    )
    cached = result.scalar_one_or_none()

    if cached:
        logger.info(f"Returning cached stats for {current_user.email}: {cached.total_trades} trades, WR={cached.win_rate}")
        return {
            "total_trades": cached.total_trades,
            "win_rate": cached.win_rate,
            "total_pnl": cached.total_pnl,
            "max_drawdown": cached.max_drawdown,
            "sharpe_ratio": cached.sharpe_ratio,
            "profit_factor": cached.profit_factor,
            "avg_rr": cached.avg_rr,
            "tp1_hit_rate": cached.tp1_hit_rate,
            "tp2_hit_rate": cached.tp2_hit_rate,
            "tp3_hit_rate": cached.tp3_hit_rate,
            "tp4_hit_rate": cached.tp4_hit_rate,
            "tp5_hit_rate": cached.tp5_hit_rate,
            "max_consec_wins": cached.max_consec_wins,
            "max_consec_losses": cached.max_consec_losses,
        }

    # Compute live from trades
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
