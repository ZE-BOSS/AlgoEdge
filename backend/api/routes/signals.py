"""
backend/api/routes/signals.py

Signal listing and detail endpoints.
Source: TradingBot_MasterPlan-2.md Section 6 — REST API
"""


from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import Signal, Trade, User
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["signals"])


@router.get("/signals")
async def get_signals(
    symbol: str | None = None,
    status: str | None = None,  # "executed", "skipped", or None for all
    session: str | None = None,
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
        # When the session this signal fired in closes. A signal taken 20
        # minutes before the NY close is a different proposition from one taken
        # at the open, and the list had no way to show that.
        "session_close_time": s.session_close_time,
        "entry_snapshot": s.entry_snapshot,
        "signal_time": s.signal_time,
        "created_at": s.created_at,
        "chart_data": s.chart_data,
    } for s in signals]


@router.get("/signals/export")
async def export_signals(
    symbol: str | None = None,
    strategy_id: str | None = None,
    timeframe: str | None = None,
    acted_on: bool | None = None,
    days: int = Query(365, ge=1, le=3650),
    limit: int = Query(20000, le=100000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Every signal the strategies raised, taken or not, as training material.

    Defined BEFORE `/signals/{signal_id}`: FastAPI matches in order, and a
    later definition would have "export" parsed as a signal id and refused.

    WHY THE SKIPPED ONES MATTER MOST
    A trade journal only contains what was taken, so it can only ever teach
    "of what I traded, what worked". The signals that were SKIPPED - and the
    gate that skipped each one - are the only record of what the filters cost.
    An agent tuning those filters needs both halves, which is why `acted_on`
    is a filter rather than a precondition.

    For a signal that became a trade the outcome is joined on, and for a
    hand-closed trade so is its aftermath, so one row carries the whole chain:
    what was seen, whether it was taken, why not, and what happened.
    """
    import json
    from datetime import datetime, timedelta

    from backend.data.models import TradeAftermath

    since = datetime.utcnow() - timedelta(days=days)
    q = select(Signal).where(Signal.user_id == current_user.id,
                             Signal.created_at >= since)
    if symbol:
        q = q.where(Signal.symbol == symbol)
    if strategy_id:
        q = q.where(Signal.strategy_id == strategy_id)
    if timeframe:
        q = q.where(Signal.timeframe == timeframe)
    if acted_on is not None:
        q = q.where(Signal.acted_on == acted_on)
    sigs = (await db.execute(q.order_by(Signal.created_at).limit(limit))).scalars().all()

    from backend.data.models import SignalOutcome

    outcomes = {}
    if sigs:
        outcomes = {o.signal_id: o for o in (await db.execute(
            select(SignalOutcome).where(SignalOutcome.signal_id.in_(
                [s_.id for s_ in sigs])))).scalars().all()}

    trade_ids = [s_.trade_id for s_ in sigs if s_.trade_id]
    trades = {}
    after = {}
    if trade_ids:
        trades = {t.id: t for t in (await db.execute(
            select(Trade).where(Trade.id.in_(trade_ids)))).scalars().all()}
        after = {a.trade_id: a for a in (await db.execute(
            select(TradeAftermath).where(TradeAftermath.trade_id.in_(trade_ids)))).scalars().all()}

    def _breakdown(raw):
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw

    rows = []
    skip_reasons: dict = {}
    for sg in sigs:
        t = trades.get(sg.trade_id) if sg.trade_id else None
        a = after.get(sg.trade_id) if sg.trade_id else None
        if not sg.acted_on and sg.skip_reason:
            # The first clause of a "; "-joined list is the gate that fired;
            # counting the whole string would split one gate across many keys.
            key = str(sg.skip_reason).split(";")[0].strip()[:80]
            skip_reasons[key] = skip_reasons.get(key, 0) + 1
        rows.append({
            "signal_id": sg.id, "symbol": sg.symbol, "strategy_id": sg.strategy_id,
            "timeframe": sg.timeframe, "direction": sg.direction,
            "signal_type": sg.signal_type,
            "signal_time": (sg.signal_time or sg.created_at).isoformat()
                if (sg.signal_time or sg.created_at) else None,
            "price_at_signal": sg.price_at_signal, "entry_price": sg.entry_price,
            "stop_loss": sg.stop_loss,
            "take_profits": [x for x in (sg.tp1_price, sg.tp2_price, sg.tp3_price,
                                         sg.tp4_price, sg.tp5_price) if x],
            "htf_bias": sg.htf_bias, "session": sg.session,
            "confluence_score": sg.confluence_score,
            "confluence_breakdown": _breakdown(sg.confluence_breakdown),
            "acted_on": bool(sg.acted_on), "skip_reason": sg.skip_reason,
            "trade": None if t is None else {
                "trade_id": t.id, "entry_price": t.entry_price,
                "exit_price": t.exit_price, "pnl": t.pnl,
                "exit_reason": t.exit_reason, "risk_reward": t.risk_reward,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            },
            # What the signal's OWN plan would have done - for a skipped
            # signal, this is what the gate gave up.
            "plan_outcome": None if sg.id not in outcomes else {
                "outcome_r": outcomes[sg.id].outcome_r,
                "hit_tp": outcomes[sg.id].hit_tp, "hit_sl": outcomes[sg.id].hit_sl,
                "best_r": outcomes[sg.id].best_r,
                "watch_complete": outcomes[sg.id].watch_complete,
                "note": outcomes[sg.id].note,
            },
            "aftermath": None if a is None else {
                "verdict": a.verdict, "closed_r": a.closed_r, "best_r": a.best_r,
                "forgone_r": a.forgone_r, "hit_tp": a.hit_tp, "hit_sl": a.hit_sl,
                "closed_by": a.closed_by,
            },
        })

    taken = sum(1 for r in rows if r["acted_on"])
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "what_this_is": (
            "Every signal raised, filtered, with the trade it became (if any) "
            "and the post-close measurement for hand-closed trades. Skipped "
            "signals carry the gate that stopped them - the only record of "
            "what the filters cost."),
        "filters": {"symbol": symbol, "strategy_id": strategy_id,
                    "timeframe": timeframe, "acted_on": acted_on, "days": days},
        "counts": {"signals": len(rows), "taken": taken,
                   "skipped": len(rows) - taken,
                   "truncated_at_limit": len(rows) >= limit},
        "skipped_by_gate": dict(sorted(skip_reasons.items(), key=lambda kv: -kv[1])),
        "signals": rows,
    }


@router.post("/signals/outcomes/run")
async def run_signal_outcomes(
    days: int = Query(30, ge=1, le=1095),
    limit: int = Query(300, le=5000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Score every signal's own plan, now. Idempotent; a bigger `days` backfills."""
    from backend.services import signal_outcome as SO

    return await SO.sweep(db, user_id=current_user.id, lookback_days=days, limit=limit)


@router.get("/signals/gate-cost")
async def signal_gate_cost(
    symbol: str | None = None,
    strategy_id: str | None = None,
    days: int = Query(90, ge=1, le=3650),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """What each gate gave up, by the signals it skipped.

    Answers the question no other screen can: is this filter earning its
    place? `net_r` positive means the skipped signals would have made money -
    the gate is costing you. Read the `assumption` before acting on it.
    """
    from datetime import datetime, timedelta

    from backend.data.models import SignalOutcome
    from backend.services import signal_outcome as SO

    since = datetime.utcnow() - timedelta(days=days)
    q = select(SignalOutcome).where(SignalOutcome.user_id == current_user.id,
                                    SignalOutcome.signal_time >= since)
    if symbol:
        q = q.where(SignalOutcome.symbol == symbol)
    if strategy_id:
        q = q.where(SignalOutcome.strategy_id == strategy_id)
    rows = (await db.execute(q)).scalars().all()
    taken = [r for r in rows if r.acted_on and r.watch_complete and r.outcome_r is not None]
    return {
        "gates": SO.gate_cost(rows),
        "taken_signals": {
            "n": len(taken),
            "net_r": round(sum(r.outcome_r for r in taken), 3),
            "mean_r": round(sum(r.outcome_r for r in taken) / len(taken), 4) if taken else None,
        },
        "still_watching": sum(1 for r in rows if not r.watch_complete),
        "assumption": (
            "Skipped signals are scored as if filled at their entry price at "
            "signal time. A gate that mostly skips entries that were never "
            "reached will look worse here than it really is."),
    }


@router.get("/signals/{signal_id}")
async def get_signal_detail(
    signal_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full signal detail including linked trade info."""
    from fastapi import HTTPException
    result = await db.execute(
        select(Signal).where(Signal.id == signal_id, Signal.user_id == current_user.id)
    )
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

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
                # Outcome detail the signal detail view could not show.
                "pnl_pips": trade.pnl_pips,
                "balance_before": trade.balance_before,
                "balance_after": trade.balance_after,
                "status": trade.status,
                "session": trade.session,
                "session_close_time": trade.session_close_time,
                "duration_seconds": (
                    (trade.exit_time - trade.entry_time).total_seconds()
                    if trade.exit_time and trade.entry_time else None
                ),
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
        "session_close_time": signal.session_close_time,
        "signal_time": signal.signal_time,
        "entry_snapshot": signal.entry_snapshot,
        "chart_data": signal.chart_data,
        "linked_trade": linked_trade,
    }


@router.get("/signals/{signal_id}/snapshot")
async def get_signal_snapshot(
    signal_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get signal entry chart snapshot image."""
    import os

    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    result = await db.execute(
        select(Signal).where(Signal.id == signal_id, Signal.user_id == current_user.id)
    )
    signal = result.scalar_one_or_none()
    if not signal or not signal.entry_snapshot or not os.path.exists(signal.entry_snapshot):
        raise HTTPException(status_code=404, detail="Snapshot not available")

    return FileResponse(signal.entry_snapshot)
