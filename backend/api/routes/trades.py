"""
backend/api/routes/trades.py

Trade history and live positions API.
Source: TradingBot_MasterPlan-2.md Section 6 — REST API Endpoints
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from typing import Optional, List
from datetime import datetime

from backend.data.database import get_db
from backend.data.models import Trade, TradePosition, User
from backend.api.deps import get_current_user
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/trades")
async def get_trades(
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get trade history with optional filters."""
    logger.info(f"Fetching trades for {current_user.email} | symbol={symbol} status={status} limit={limit}")
    query = select(Trade).options(selectinload(Trade.positions)).where(Trade.user_id == current_user.id)

    if symbol:
        query = query.where(Trade.symbol == symbol)
    if status:
        query = query.where(Trade.status == status)

    query = query.order_by(desc(Trade.created_at)).limit(limit).offset(offset)
    result = await db.execute(query)
    trades = result.scalars().all()
    logger.info(f"Returning {len(trades)} trades for {current_user.email}")

    return [{
        "id": t.id,
        "symbol": t.symbol,
        "direction": t.direction,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "stop_loss": t.stop_loss,
        "take_profit": t.take_profit,
        "volume": t.volume,
        "pnl": t.pnl,
        "pnl_pips": t.pnl_pips,
        "realized_pnl": sum(p.pnl for p in t.positions if p.status == "CLOSED" and p.pnl is not None) if t.status == "OPEN" else t.pnl,
        "risk_reward": t.risk_reward,
        "status": t.status,
        "exit_reason": t.exit_reason,
        "entry_time": t.entry_time,
        "exit_time": t.exit_time,
        "mt5_ticket": t.mt5_ticket,
        "entry_snapshot": t.entry_snapshot,
        "exit_snapshot": t.exit_snapshot,
        "balance_before": t.balance_before,
        "balance_after": t.balance_after,
        "confluence_score": t.confluence_score,
        "chart_data": t.chart_data,
        "created_at": t.created_at,
    } for t in trades]


@router.get("/positions")
async def get_open_positions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current open positions with sub-position details (N+1 fix: uses selectinload)."""
    logger.info(f"Fetching open positions for {current_user.email}")
    query = (
        select(Trade)
        .options(selectinload(Trade.positions))
        .where(Trade.user_id == current_user.id, Trade.status == "OPEN")
        .order_by(desc(Trade.created_at))
    )
    result = await db.execute(query)
    trades = result.scalars().all()

    positions = []
    for t in trades:
        positions.append({
            "trade": {
                "id": t.id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "stop_loss": t.stop_loss,
                "volume": t.volume,
                "confluence_score": t.confluence_score,
            },
            "sub_positions": [{
                "tp_level": sp.tp_level,
                "volume": sp.volume,
                "take_profit": sp.take_profit,
                "status": sp.status,
                "be_applied": sp.be_applied,
                "trail_method": sp.trail_method,
                "trail_activated": sp.trail_activated,
            } for sp in t.positions],
        })

    return positions


@router.get("/trades/{trade_id}/snapshot/{snapshot_type}")
async def get_trade_snapshot(
    trade_id: int,
    snapshot_type: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get entry or exit chart snapshot path."""
    import os
    from fastapi.responses import FileResponse
    from fastapi import HTTPException

    result = await db.execute(
        select(Trade).where(Trade.id == trade_id, Trade.user_id == current_user.id)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    path = trade.entry_snapshot if snapshot_type == "entry" else trade.exit_snapshot
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Snapshot not available")

    return FileResponse(path)

@router.get("/force-close-all")
async def force_close_all_stuck_trades(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Force closes all OPEN trades to unblock the system."""
    query = select(Trade).where(Trade.user_id == current_user.id, Trade.status == "OPEN")
    result = await db.execute(query)
    trades = result.scalars().all()
    
    count = 0
    for t in trades:
        t.status = "CLOSED"
        t.exit_reason = "MANUAL_OVERRIDE"
        t.exit_time = datetime.utcnow()
"""
backend/api/routes/trades.py

Trade history and live positions API.
Source: TradingBot_MasterPlan-2.md Section 6 — REST API Endpoints
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from typing import Optional, List
from datetime import datetime

from backend.data.database import get_db
from backend.data.models import Trade, TradePosition, User
from backend.api.deps import get_current_user
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/trades")
async def get_trades(
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get trade history with optional filters."""
    logger.info(f"Fetching trades for {current_user.email} | symbol={symbol} status={status} limit={limit}")
    query = select(Trade).options(selectinload(Trade.positions)).where(Trade.user_id == current_user.id)

    if symbol:
        query = query.where(Trade.symbol == symbol)
    if status:
        query = query.where(Trade.status == status)

    query = query.order_by(desc(Trade.created_at)).limit(limit).offset(offset)
    result = await db.execute(query)
    trades = result.scalars().all()
    logger.info(f"Returning {len(trades)} trades for {current_user.email}")

    return [{
        "id": t.id,
        "symbol": t.symbol,
        "direction": t.direction,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "stop_loss": t.stop_loss,
        "take_profit": t.take_profit,
        "volume": t.volume,
        "pnl": t.pnl,
        "pnl_pips": t.pnl_pips,
        "realized_pnl": sum(p.pnl for p in t.positions if p.status == "CLOSED" and p.pnl is not None) if t.status == "OPEN" else t.pnl,
        "risk_reward": t.risk_reward,
        "status": t.status,
        "exit_reason": t.exit_reason,
        "entry_time": t.entry_time,
        "exit_time": t.exit_time,
        "mt5_ticket": t.mt5_ticket,
        "entry_snapshot": t.entry_snapshot,
        "exit_snapshot": t.exit_snapshot,
        "balance_before": t.balance_before,
        "balance_after": t.balance_after,
        "confluence_score": t.confluence_score,
        "chart_data": t.chart_data,
        "created_at": t.created_at,
    } for t in trades]


@router.get("/positions")
async def get_open_positions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current open positions with sub-position details (N+1 fix: uses selectinload)."""
    logger.info(f"Fetching open positions for {current_user.email}")
    query = (
        select(Trade)
        .options(selectinload(Trade.positions))
        .where(Trade.user_id == current_user.id, Trade.status == "OPEN")
        .order_by(desc(Trade.created_at))
    )
    result = await db.execute(query)
    trades = result.scalars().all()

    positions = []
    for t in trades:
        positions.append({
            "trade": {
                "id": t.id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "stop_loss": t.stop_loss,
                "volume": t.volume,
                "confluence_score": t.confluence_score,
            },
            "sub_positions": [{
                "tp_level": sp.tp_level,
                "volume": sp.volume,
                "take_profit": sp.take_profit,
                "status": sp.status,
                "be_applied": sp.be_applied,
                "trail_method": sp.trail_method,
                "trail_activated": sp.trail_activated,
            } for sp in t.positions],
        })

    return positions


@router.get("/trades/{trade_id}/snapshot/{snapshot_type}")
async def get_trade_snapshot(
    trade_id: int,
    snapshot_type: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get entry or exit chart snapshot path."""
    import os
    from fastapi.responses import FileResponse
    from fastapi import HTTPException

    result = await db.execute(
        select(Trade).where(Trade.id == trade_id, Trade.user_id == current_user.id)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    path = trade.entry_snapshot if snapshot_type == "entry" else trade.exit_snapshot
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Snapshot not available")

    return FileResponse(path)

@router.get("/force-close-all")
async def force_close_all_stuck_trades(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Force closes all OPEN trades to unblock the system."""
    query = select(Trade).where(Trade.user_id == current_user.id, Trade.status == "OPEN")
    result = await db.execute(query)
    trades = result.scalars().all()
    
    count = 0
    for t in trades:
        t.status = "CLOSED"
        t.exit_reason = "MANUAL_OVERRIDE"
        t.exit_time = datetime.utcnow()
        count += 1
        
        # Also close positions
        pos_query = select(TradePosition).where(TradePosition.parent_trade_id == t.id)
        pos_res = await db.execute(pos_query)
        for p in pos_res.scalars().all():
            if p.status == "OPEN":
                p.status = "CLOSED"
                p.exit_time = datetime.utcnow()
                
    await db.commit()
    return {"message": f"Successfully closed {count} stuck trades. The bot is now unblocked."}
