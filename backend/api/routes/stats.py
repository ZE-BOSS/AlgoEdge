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
    # Magic-number base identifying orders this bot placed — used to keep other
    # people's trades out of this user's performance stats.
    _magic_base = 1001
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
            _magic_base = int(getattr(_parsed, "magic_base", 1001) or 1001)
    except Exception as e:
        logger.warning(f"Could not resolve sizing_basis for stats, defaulting to STATIC: {e}")
    # DB Cache check removed: Always compute live from MT5 for accuracy.

    # 1. Try to get deals from MT5 history first
    try:
        from backend.mt5.order_manager import OrderManager
        from backend.services.trade_ownership import load_bot_tickets

        # Bot-owned deals only. This used to pull the account's ENTIRE deal
        # history unfiltered, so connecting to an account that already had a
        # trading history made the dashboard report that history as this bot's
        # performance — win rate, P&L, drawdown, all of it. Ownership is
        # decided by backend/services/trade_ownership.py.
        _known = await load_bot_tickets(db, current_user.id)
        deals = await OrderManager.get_closed_positions_since(
            0, bot_only=True, known_tickets=_known, magic_base=_magic_base
        )


        if deals:
            # Fetch balance operations to calculate net deposits
            balance_ops = await OrderManager.get_balance_operations_since(0)
            net_deposits = sum(op.get("profit", 0.0) for op in balance_ops)
            
            # We got MT5 data! Sort chronologically
            deals.sort(key=lambda x: x["time"])
            
            # Fetch MT5 live balance
            import MetaTrader5 as mt5
            from backend.mt5.executor import run_mt5
            # On the shared MT5 thread. Called bare, this ran on the event-loop
            # thread while OrderManager's history fetch ran on the MT5 thread —
            # two threads into a connection that belongs to one, which is what
            # produced "returned a result with an exception set" here.
            account_info = await run_mt5(mt5.account_info)
            live_balance = account_info.balance if account_info else 10000.0
            
            total_mt5_pnl = sum((d["profit"] + d["commission"] + d["swap"]) for d in deals)
            # Starting balance for THIS BOT's equity curve: the live balance
            # with the bot's own realised P&L backed out.
            #
            # The old formula also subtracted net_deposits, which was correct
            # only while `deals` was the account's entire history. Now that
            # `deals` is bot-owned trades only, the account balance also
            # contains P&L the bot did not produce; subtracting deposits on top
            # of that double-counts. `live_balance - total_mt5_pnl` answers the
            # question the dashboard is actually asking — "what was the balance
            # before the bot's trades" — for an account whose non-bot activity
            # is already baked into the current balance.
            initial_balance = live_balance - total_mt5_pnl
            if initial_balance <= 0:
                initial_balance = live_balance if live_balance > 0 else 10000.0
                
            # Fetch local DB trades to get the accurate exit_reason (e.g., TP1, TP2) instead of just "TP"
            #
            # This read `t.position_id`, which is not a column on Trade (the MT5
            # position ticket is stored as `mt5_ticket`, and per-leg on
            # TradePosition.mt5_ticket). Every call therefore raised
            # AttributeError here, was swallowed by the `except Exception` below,
            # and logged "Could not compute stats from MT5 ... Falling back to
            # DB" — so this MT5 stats path never actually returned once the
            # account had any deals at all.
            from backend.data.models import TradePosition as _TP

            db_trades_result = await db.execute(
                select(Trade).where(Trade.user_id == current_user.id, Trade.status == "CLOSED")
            )
            _closed = db_trades_result.scalars().all()
            db_trades = {
                str(t.mt5_ticket): t.exit_reason for t in _closed if t.mt5_ticket
            }
            # Multi-TP trades hold their MT5 tickets on the legs, not the parent.
            _legs = await db.execute(
                select(_TP.mt5_ticket, _TP.tp_level, _TP.parent_trade_id).where(
                    _TP.user_id == current_user.id, _TP.mt5_ticket.isnot(None)
                )
            )
            _reason_by_trade = {t.id: t.exit_reason for t in _closed}
            for _tk, _lvl, _parent in _legs.all():
                db_trades.setdefault(
                    str(_tk), _reason_by_trade.get(_parent) or (f"TP{_lvl}" if _lvl else None)
                )
                
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
