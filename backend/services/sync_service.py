"""
backend/services/sync_service.py

Reconciles local Trade database with actual MT5 terminal history.
Ensures PnL, swap, commission, exit times and exact prices are 100% accurate.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.data.models import Trade, TradePosition, User
from backend.utils.encryption import get_encryption_service
from backend.utils.logger import get_logger

logger = get_logger(__name__)


async def sync_mt5_history(user: User, db: AsyncSession, hours_back: int = 72) -> dict:
    """
    Connect to MT5 (if configured), fetch deals for the last `hours_back` hours,
    and update local Trade and TradePosition records with actual execution data.
    """
    if not user.mt5_account or not user.mt5_password_encrypted or not user.mt5_server:
        logger.warning(f"MT5 Sync aborted for {user.email}: Credentials missing")
        return {"status": "skipped", "reason": "No credentials"}

    try:
        import MetaTrader5 as mt5
    except ImportError:
        logger.error("MT5 Sync aborted: MetaTrader5 package not installed")
        return {"status": "error", "reason": "MetaTrader5 not installed"}

    loop = asyncio.get_event_loop()
    
    # Check if we are already connected to the correct account
    acc_info = await loop.run_in_executor(None, mt5.account_info)
    is_already_connected = (acc_info is not None and acc_info.login == user.mt5_account)

    if not is_already_connected:
        encryption = get_encryption_service()
        
        try:
            password = encryption.decrypt(user.mt5_password_encrypted)
        except Exception as e:
            logger.error(f"Failed to decrypt MT5 password for {user.email}: {e}")
            return {"status": "error", "reason": "Decryption failed"}

        # Connect to MT5
        if user.mt5_path:
            init_ok = await loop.run_in_executor(None, lambda: mt5.initialize(path=user.mt5_path))
        else:
            init_ok = await loop.run_in_executor(None, mt5.initialize)

        if not init_ok:
            logger.error(f"MT5 Init failed for sync: {mt5.last_error()}")
            return {"status": "error", "reason": f"MT5 Init failed: {mt5.last_error()}"}

        login_ok = await loop.run_in_executor(
            None,
            lambda: mt5.login(user.mt5_account, password=password, server=user.mt5_server)
        )

        if not login_ok:
            logger.error(f"MT5 Login failed for sync: {mt5.last_error()}")
            await loop.run_in_executor(None, mt5.shutdown)
            return {"status": "error", "reason": f"MT5 Login failed: {mt5.last_error()}"}

    now = datetime.now(timezone.utc)
    from_date = now - timedelta(hours=hours_back)
    
    # Fetch historical deals
    deals = await loop.run_in_executor(None, lambda: mt5.history_deals_get(from_date, now))
    
    # Also fetch account info to sync live balance/equity
    account_info = await loop.run_in_executor(None, mt5.account_info)
    
    if not is_already_connected:
        await loop.run_in_executor(None, mt5.shutdown)

    if deals is None:
        logger.warning(f"No deals returned from MT5 for {user.email}")
        return {"status": "ok", "synced_positions": 0, "account": account_info._asdict() if account_info else {}}

    deals_list = list(deals)
    
    # Group deals by position_id
    # MT5 represents a trade as a "Position" consisting of one entry "Deal" and one or more exit "Deals".
    position_pnl = {}
    
    for deal in deals_list:
        # Deal Entry/Exit: 0 = DEAL_ENTRY_IN, 1 = DEAL_ENTRY_OUT, 2 = DEAL_ENTRY_INOUT
        if deal.position_id == 0:
            continue
            
        pos_id = str(deal.position_id)
        if pos_id not in position_pnl:
            position_pnl[pos_id] = {
                "profit": 0.0,
                "swap": 0.0,
                "commission": 0.0,
                "exit_price": None,
                "exit_time": None,
                "is_closed": False,
            }
        
        position_pnl[pos_id]["profit"] += deal.profit
        position_pnl[pos_id]["swap"] += deal.swap
        position_pnl[pos_id]["commission"] += deal.commission
        
        if deal.entry == 1 or deal.entry == 2:  # OUT or INOUT
            position_pnl[pos_id]["is_closed"] = True
            position_pnl[pos_id]["exit_price"] = deal.price
            # Convert MT5 deal time to naive UTC datetime for Postgres
            position_pnl[pos_id]["exit_time"] = datetime.fromtimestamp(deal.time, timezone.utc).replace(tzinfo=None)
            
    # Now query the local DB for recent trades to sync
    query = (
        select(Trade)
        .options(selectinload(Trade.positions))
        .where(Trade.user_id == user.id)
        .where(Trade.created_at >= from_date.replace(tzinfo=None) - timedelta(days=5)) # Look back a bit further for entries
    )
    
    result = await db.execute(query)
    trades = result.scalars().all()
    
    synced_count = 0
    updated = False
    
    for trade in trades:
        trade_updated = False
        
        # Check sub-positions first
        for pos in trade.positions:
            if not pos.mt5_ticket:
                continue
                
            ticket = str(pos.mt5_ticket)
            if ticket in position_pnl:
                mt5_data = position_pnl[ticket]
                
                # Update sub-position
                if mt5_data["is_closed"] and pos.status == "OPEN":
                    pos.status = "CLOSED"
                    pos.exit_price = mt5_data["exit_price"]
                    pos.exit_time = mt5_data["exit_time"]
                    
                new_pnl = mt5_data["profit"] + mt5_data["swap"] + mt5_data["commission"]
                if pos.pnl != new_pnl:
                    pos.pnl = new_pnl
                    
                trade_updated = True
                
        # Handle parent trade logic
        if trade.positions:
            if trade_updated:
                # Re-calculate parent trade aggregate PnL
                closed_pnl = sum(p.pnl for p in trade.positions if p.pnl is not None)
                if trade.pnl != closed_pnl:
                    trade.pnl = closed_pnl
                
                # Derive parent status from sub-positions
                all_closed = all(p.status == "CLOSED" for p in trade.positions)
                if all_closed and trade.status == "OPEN":
                    trade.status = "CLOSED"
                    trade.exit_reason = "MT5_SYNC_CLOSED"
                    last_exit = max((p.exit_time for p in trade.positions if p.exit_time), default=None)
                    if last_exit:
                        trade.exit_time = last_exit
        else:
            # Single-position trade (no sub-positions)
            if trade.mt5_ticket:
                ticket = str(trade.mt5_ticket)
                if ticket in position_pnl:
                    mt5_data = position_pnl[ticket]
                    
                    if mt5_data["is_closed"] and trade.status == "OPEN":
                        trade.status = "CLOSED"
                        trade.exit_price = mt5_data["exit_price"]
                        trade.exit_time = mt5_data["exit_time"]
                        if trade.exit_reason is None:
                            trade.exit_reason = "MT5_SYNC_CLOSED"
                    
                    new_pnl = mt5_data["profit"] + mt5_data["swap"] + mt5_data["commission"]
                    if trade.pnl != new_pnl:
                        trade.pnl = new_pnl
                    
                    trade_updated = True
                
        if trade_updated:
            trade._mt5_verified = True
            synced_count += 1
            updated = True
            
    if updated:
        await db.commit()
        
    logger.info(f"MT5 Sync complete for {user.email}: {synced_count} trades synced/verified")
    
    return {
        "status": "ok",
        "synced_positions": synced_count,
        "account": account_info._asdict() if account_info else {}
    }
