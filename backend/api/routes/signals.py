"""
backend/api/routes/signals.py

Signal listing and detail endpoints.
Source: TradingBot_MasterPlan-2.md Section 6 — REST API
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional

from backend.data.database import get_db
from backend.data.models import Signal, Trade, User
from backend.api.deps import get_current_user
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["signals"])


@router.get("/signals")
async def get_signals(
    symbol: Optional[str] = None,
    status: Optional[str] = None,  # "executed", "skipped", or None for all
    session: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List signals for the authenticated user with optional filters."""
    logger.info(f"Fetching signals for {current_user.email} | status={status} symbol={symbol}")
    query = select(Signal).where(Signal.user_id == current_user.id)

    if symbol:
        query = query.where(Signal.symbol == symbol)
    if status == "executed":
        query = query.where(Signal.acted_on == True)
    elif status == "skipped":
        query = query.where(Signal.acted_on == False)
    if session:
        query = query.where(Signal.session == session.upper())

    query = query.order_by(desc(Signal.created_at)).limit(limit).offset(offset)
    result = await db.execute(query)
    signals = result.scalars().all()

    return [{
        "id": s.id,
        "symbol": s.symbol,
        "direction": s.direction,
        "signal_type": s.signal_type,
        "timeframe": s.timeframe,
        "entry_price": s.entry_price,
        "stop_loss": s.stop_loss,
        "tp1_price": s.tp1_price,
        "tp2_price": s.tp2_price,
        "tp3_price": s.tp3_price,
        "tp4_price": s.tp4_price,
        "tp5_price": s.tp5_price,
        "ob_top": s.ob_top,
        "ob_bottom": s.ob_bottom,
        "fvg_top": s.fvg_top,
        "fvg_bottom": s.fvg_bottom,
        "htf_bias": s.htf_bias,
        "confluence_score": s.confluence_score,
        "confluence_breakdown": s.confluence_breakdown,
        "acted_on": s.acted_on,
        "skip_reason": s.skip_reason,
        "trade_id": s.trade_id,
        "session": s.session,
        "entry_snapshot": s.entry_snapshot,
        "signal_time": s.signal_time,
    } for s in signals]


@router.get("/signals/{signal_id}")
async def get_signal_detail(
    signal_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full signal detail including linked trade info."""
    result = await db.execute(
        select(Signal).where(Signal.id == signal_id, Signal.user_id == current_user.id)
    )
    signal = result.scalar_one_or_none()
    if not signal:
        return {"error": "Signal not found"}

    # If executed, get linked trade info
    linked_trade = None
    if signal.trade_id:
        trade_result = await db.execute(select(Trade).where(Trade.id == signal.trade_id))
        trade = trade_result.scalar_one_or_none()
        if trade:
            linked_trade = {
                "id": trade.id,
                "pnl": trade.pnl,
                "risk_reward": trade.risk_reward,
                "exit_reason": trade.exit_reason,
                "exit_price": trade.exit_price,
                "entry_time": trade.entry_time,
                "exit_time": trade.exit_time,
                "entry_snapshot": trade.entry_snapshot,
                "exit_snapshot": trade.exit_snapshot,
            }

    return {
        "id": signal.id,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "signal_type": signal.signal_type,
        "timeframe": signal.timeframe,
        "entry_price": signal.entry_price,
        "stop_loss": signal.stop_loss,
        "tp1_price": signal.tp1_price,
        "tp2_price": signal.tp2_price,
        "tp3_price": signal.tp3_price,
        "tp4_price": signal.tp4_price,
        "tp5_price": signal.tp5_price,
        "ob_top": signal.ob_top,
        "ob_bottom": signal.ob_bottom,
        "fvg_top": signal.fvg_top,
        "fvg_bottom": signal.fvg_bottom,
        "htf_bias": signal.htf_bias,
        "confluence_score": signal.confluence_score,
        "confluence_breakdown": signal.confluence_breakdown,
        "acted_on": signal.acted_on,
        "skip_reason": signal.skip_reason,
        "session": signal.session,
        "entry_snapshot": signal.entry_snapshot,
        "signal_time": signal.signal_time,
        "linked_trade": linked_trade,
    }


@router.get("/signals/{signal_id}/snapshot")
async def get_signal_snapshot(
    signal_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get signal entry chart snapshot image."""
    from fastapi.responses import FileResponse

    result = await db.execute(
        select(Signal).where(Signal.id == signal_id, Signal.user_id == current_user.id)
    )
    signal = result.scalar_one_or_none()
    if not signal or not signal.entry_snapshot:
        return {"error": "Snapshot not available"}

    return FileResponse(signal.entry_snapshot)
