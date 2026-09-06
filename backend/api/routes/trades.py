"""
backend/api/routes/trades.py

Trade history and live positions API.
Source: TradingBot_MasterPlan-2.md Section 6 — REST API Endpoints
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import Trade, TradePosition, User
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/trades")
async def get_trades(
    symbol: str | None = None,
    status: str | None = None,
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
        # Journal detail the UI asked for and the API never sent.
        "strategy_id": t.strategy_id,
        "session": t.session,
        "session_close_time": t.session_close_time,
        "duration_seconds": (
            (t.exit_time - t.entry_time).total_seconds()
            if t.exit_time and t.entry_time else None
        ),
        "positions": [{
            "tp_level": p.tp_level,
            "mt5_ticket": p.mt5_ticket,
            "volume": p.volume,
            "entry_price": p.entry_price,
            "stop_loss": p.stop_loss,
            "take_profit": p.take_profit,
            "exit_price": p.exit_price,
            "exit_time": p.exit_time,
            "exit_reason": p.exit_reason,
            "pnl": p.pnl,
            "pnl_pips": p.pnl_pips,
            "planned_rr": p.planned_rr,
            "realized_rr": p.realized_rr,
            "status": p.status,
            "be_applied": p.be_applied,
            "trail_method": p.trail_method,
            "trail_activated": p.trail_activated,
        } for p in sorted(t.positions, key=lambda x: x.tp_level or 0)],
    } for t in trades]


@router.get("/trades/summary")
async def get_trades_summary(
    symbol: str | None = None,
    days: int | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Journal summary — the totals row the journal page had no data for.

    Deliberately computed over CLOSED trades only: an open trade has no realised
    P&L, and including floating P&L in a win-rate makes the number move on every
    tick.
    """
    from datetime import timedelta

    q = select(Trade).where(Trade.user_id == current_user.id, Trade.status == "CLOSED")
    if symbol:
        q = q.where(Trade.symbol == symbol)
    if days:
        q = q.where(Trade.entry_time >= datetime.utcnow() - timedelta(days=days))
    trades = (await db.execute(q.order_by(Trade.entry_time.asc()))).scalars().all()

    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "breakeven": 0, "win_rate": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0, "net_pnl": 0.0,
            "profit_factor": None, "expectancy": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "largest_win": 0.0, "largest_loss": 0.0, "total_pips": 0.0,
            "avg_rr": None, "max_drawdown": 0.0, "max_drawdown_pct": None,
            "balance_start": None, "balance_end": None,
            "by_symbol": [], "by_strategy": [], "by_session": [],
        }

    pnls = [t.pnl or 0.0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)

    # Peak-to-trough on the realised equity curve.
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    balance_start = next((t.balance_before for t in trades if t.balance_before), None)
    balance_end = next((t.balance_after for t in reversed(trades) if t.balance_after), None)

    def _group(key_fn):
        buckets: dict = {}
        for t in trades:
            k = key_fn(t) or "UNKNOWN"
            b = buckets.setdefault(k, {"key": k, "trades": 0, "wins": 0, "pnl": 0.0})
            b["trades"] += 1
            b["pnl"] += t.pnl or 0.0
            if (t.pnl or 0.0) > 0:
                b["wins"] += 1
        for b in buckets.values():
            b["win_rate"] = round(100.0 * b["wins"] / b["trades"], 2) if b["trades"] else 0.0
            b["pnl"] = round(b["pnl"], 2)
        return sorted(buckets.values(), key=lambda x: -x["pnl"])

    rrs = [t.risk_reward for t in trades if t.risk_reward is not None]
    pips = [t.pnl_pips for t in trades if t.pnl_pips is not None]

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(pnls) - len(wins) - len(losses),
        "win_rate": round(100.0 * len(wins) / len(trades), 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(net, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "expectancy": round(net / len(trades), 2),
        "avg_win": round(gross_profit / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "largest_win": round(max(pnls), 2),
        "largest_loss": round(min(pnls), 2),
        "total_pips": round(sum(pips), 1) if pips else 0.0,
        "avg_rr": round(sum(rrs) / len(rrs), 2) if rrs else None,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": (
            round(100.0 * max_dd / balance_start, 2) if balance_start else None
        ),
        "balance_start": balance_start,
        "balance_end": balance_end,
        "by_symbol": _group(lambda t: t.symbol),
        "by_strategy": _group(lambda t: t.strategy_id),
        "by_session": _group(lambda t: t.session),
    }


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

    from fastapi import HTTPException
    from fastapi.responses import FileResponse

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
    """Force closes all OPEN trades and their sub-positions to unblock the system."""
    query = select(Trade).where(Trade.user_id == current_user.id, Trade.status == "OPEN")
    result = await db.execute(query)
    trades = result.scalars().all()
    
    count = 0
    for t in trades:
        t.status = "CLOSED"
        t.exit_reason = "MANUAL_OVERRIDE"
        t.exit_time = datetime.utcnow()
        count += 1
        
        # Also close all sub-positions
        pos_query = select(TradePosition).where(TradePosition.parent_trade_id == t.id)
        pos_res = await db.execute(pos_query)
        for p in pos_res.scalars().all():
            if p.status == "OPEN":
                p.status = "CLOSED"
                p.exit_time = datetime.utcnow()
                
    await db.commit()
    logger.info(f"Force-closed {count} stuck trades for {current_user.email}")
    return {"message": f"Successfully closed {count} stuck trades. The bot is now unblocked."}
