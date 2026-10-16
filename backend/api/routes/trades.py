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


# ── What happened after a hand-closed trade ─────────────────────────────────
#
# A trade the stop or the target closed was decided by the plan. A trade a
# person or an agent CLOSED is the one decision made by hand, and it was the
# only one never scored - the journal records the exit and nothing after it.
# `services/trade_aftermath.py` measures the window the trade could still have
# been held; these routes read it, refresh it, and hand it out for training.


@router.get("/trades/aftermath")
async def list_aftermath(
    symbol: str | None = None,
    strategy_id: str | None = None,
    verdict: str | None = None,
    closed_by: str | None = None,
    days: int = Query(90, ge=1, le=1095),
    limit: int = Query(200, le=2000),
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The scored hand-closes, newest first, with a summary over the filter.

    The summary is the point of the screen: one trade closed early is an
    anecdote, and `LEFT_MONEY on 61% of manual closes, median 1.4R forgone` is
    a habit worth changing.
    """
    from datetime import timedelta

    from backend.data.models import TradeAftermath

    since = datetime.utcnow() - timedelta(days=days)
    q = select(TradeAftermath).where(
        TradeAftermath.user_id == current_user.id,
        TradeAftermath.closed_at >= since)
    if symbol:
        q = q.where(TradeAftermath.symbol == symbol)
    if strategy_id:
        q = q.where(TradeAftermath.strategy_id == strategy_id)
    if verdict:
        q = q.where(TradeAftermath.verdict == verdict.upper())
    if closed_by:
        q = q.where(TradeAftermath.closed_by == closed_by.upper())

    rows = (await db.execute(
        q.order_by(desc(TradeAftermath.closed_at))
        .limit(limit).offset(offset))).scalars().all()
    every = (await db.execute(q)).scalars().all()

    def _avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    def _median(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        mid = len(vals) // 2
        return round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 4)

    # Only FINISHED rows count towards the headline numbers. A provisional
    # verdict is a guess about a window that has not happened yet and can
    # still flip, and `gate_cost` already excludes them - the two summaries
    # must not disagree about what "scored" means.
    scored = [r for r in every
              if r.verdict and r.verdict != "UNKNOWN" and r.watch_complete]
    counts: dict = {}
    for r in scored:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    return {
        "rows": [_aftermath_dict(r) for r in rows],
        "total": len(every),
        "summary": {
            "scored": len(scored),
            "by_verdict": counts,
            "pct_left_money": (round(100.0 * counts.get("LEFT_MONEY", 0) / len(scored), 1)
                               if scored else None),
            "median_forgone_r": _median([r.forgone_r for r in scored]),
            "mean_forgone_r": _avg([r.forgone_r for r in scored]),
            "reached_tp_after_close": sum(1 for r in every if r.hit_tp),
            "reached_sl_after_close": sum(1 for r in every if r.hit_sl),
            "still_watching": sum(1 for r in every if not r.watch_complete),
        },
    }


def _aftermath_dict(r) -> dict:
    return {
        "trade_id": r.trade_id, "symbol": r.symbol, "strategy_id": r.strategy_id,
        "direction": r.direction, "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        "close_reason": r.close_reason, "closed_by": r.closed_by,
        "entry_price": r.entry_price, "exit_price": r.exit_price,
        "stop_loss": r.stop_loss, "take_profit": r.take_profit,
        "closed_r": r.closed_r,
        "watch_hours": r.watch_hours, "watch_complete": r.watch_complete,
        "bars_seen": r.bars_seen,
        "hit_tp": r.hit_tp, "tp_minutes": r.tp_minutes,
        "hit_sl": r.hit_sl, "sl_minutes": r.sl_minutes,
        "best_price": r.best_price, "best_r": r.best_r, "best_minutes": r.best_minutes,
        "r_at_watch_end": r.r_at_watch_end,
        "verdict": r.verdict, "forgone_r": r.forgone_r, "note": r.note,
    }


@router.post("/trades/aftermath/run")
async def run_aftermath(
    days: int = Query(30, ge=1, le=1095),
    limit: int = Query(300, le=2000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Measure anything not yet measured, now.

    The same call backfills: a larger `days` reaches further into the journal.
    It is idempotent - rows whose watch window has elapsed are skipped - so
    running it twice costs time and changes nothing.
    """
    from backend.services import trade_aftermath as A

    return await A.sweep(db, user_id=current_user.id, lookback_days=days, limit=limit)


@router.get("/trades/journal-export")
async def export_journal(
    symbol: str | None = None,
    strategy_id: str | None = None,
    days: int = Query(365, ge=1, le=3650),
    include_open: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The journal as training material, filtered, with the aftermath joined on.

    WHY NOT THE EXISTING TRADE LIST
    That one answers "what did I do". This one answers "what should I have
    done", which needs three things the trade row does not carry: the signal
    that produced the entry, whether the exit was the plan's or a person's,
    and - for the hand-closes - what the market did next. An agent learning
    exit timing cannot be trained on a table that stops at the exit.

    Filterable by instrument and strategy because that is how it gets fed
    back: one agent per symbol and strategy, trained on its own history.
    """
    from datetime import timedelta

    from backend.data.models import TradeAftermath
    from backend.services import trade_aftermath as A

    since = datetime.utcnow() - timedelta(days=days)
    q = select(Trade).where(Trade.user_id == current_user.id,
                            Trade.entry_time >= since)
    if symbol:
        q = q.where(Trade.symbol == symbol)
    if strategy_id:
        q = q.where(Trade.strategy_id == strategy_id)
    if not include_open:
        q = q.where(Trade.exit_time.isnot(None))
    trades = (await db.execute(q.order_by(Trade.entry_time))).scalars().all()

    after = {r.trade_id: r for r in (await db.execute(
        select(TradeAftermath).where(
            TradeAftermath.user_id == current_user.id))).scalars().all()}

    rows = []
    for t in trades:
        planned = A.was_planned_exit(t.exit_reason)
        a = after.get(t.id)
        rows.append({
            "trade_id": t.id, "symbol": t.symbol, "strategy_id": t.strategy_id,
            "direction": t.direction, "volume": t.volume,
            "entry_time": t.entry_time.isoformat() if t.entry_time else None,
            "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            "entry_price": t.entry_price, "exit_price": t.exit_price,
            "stop_loss": t.stop_loss, "take_profit": t.take_profit,
            "pnl": t.pnl, "pnl_pips": t.pnl_pips, "risk_reward": t.risk_reward,
            "status": t.status, "exit_reason": t.exit_reason,
            "exit_was_planned": planned,
            "closed_by": A.closed_by(t.exit_reason) if not planned else "PLAN",
            "max_drawdown": t.max_drawdown,
            "session": t.session, "confluence_score": t.confluence_score,
            "mt5_ticket": t.mt5_ticket,
            # Only hand-closes have one, and that is the point: these rows are
            # the labelled examples an exit policy can actually learn from.
            "aftermath": _aftermath_dict(a) if a else None,
        })

    unscored = sum(1 for r in rows
                   if not r["exit_was_planned"] and r["exit_time"] and not r["aftermath"])
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "what_this_is": (
            "One trading journal, filtered, with the post-close measurement "
            "joined onto every trade a person or an agent closed by hand. "
            "`aftermath` is null for exits the plan made (stop or target) "
            "because there is nothing to second-guess about those."),
        "filters": {"symbol": symbol, "strategy_id": strategy_id, "days": days,
                    "include_open": include_open},
        "counts": {
            "trades": len(rows),
            "hand_closed": sum(1 for r in rows if not r["exit_was_planned"] and r["exit_time"]),
            "with_aftermath": sum(1 for r in rows if r["aftermath"]),
            "hand_closed_not_yet_measured": unscored,
        },
        "note": ("Run POST /api/trades/aftermath/run to measure the "
                 f"{unscored} hand-closed trade(s) still without one."
                 if unscored else ""),
        "trades": rows,
    }
