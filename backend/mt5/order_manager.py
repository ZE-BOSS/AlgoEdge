"""
backend/mt5/order_manager.py

Order placement, modification, and multi-position management logic.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backend.utils.logger import get_logger

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)

# MT5 retcodes that indicate a permanent, broker/account-side condition — retrying
# immediately will not help (the account/symbol needs to be fixed in the MT5 terminal
# or with the broker). Map each to a human-actionable hint for alerts/logs.
_PERMANENT_RETCODES: dict[int, str] = {
    10017: "Trade disabled — check that AutoTrading is enabled in the MT5 terminal and "
           "that this account/symbol has trading permissions with the broker.",
    10018: "Market closed for this symbol.",
    10019: "No money — insufficient margin/funds for this volume.",
    10027: "AutoTrading is disabled in the MT5 client terminal (toggle it on).",
    10031: "No connection to the trade server.",
    10032: "Trading is prohibited for this account (e.g. investor/read-only login).",
    10033: "Order limit for this account has been reached.",
    10034: "Volume limit for this account/symbol has been reached.",
}


class OrderManager:
    """Handles execution of orders on MT5."""

    @staticmethod
    async def place_market_order(
        symbol: str,
        direction: str,
        volume: float,
        sl: float,
        tp: float,
        magic: int,
        comment: str = ""
    ) -> dict[str, Any]:
        """Place a single market order."""
        logger.info(f"Placing {direction} on {symbol} (vol: {volume})")
        
        if not mt5:
            logger.info("MOCK MODE: Order placed successfully.")
            return {"success": True, "ticket": 12345}

        # ── IPC Auto-Recovery ──
        terminal_info = mt5.terminal_info()
        if terminal_info is None or not terminal_info.connected:
            logger.warning("MT5 connection lost in OrderManager. Attempting to re-initialize IPC...")
            mt5.initialize()
            
        action = mt5.ORDER_TYPE_BUY if direction.upper() in ("BUY", "BULLISH") else mt5.ORDER_TYPE_SELL
        
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error(f"Order failed: Invalid symbol {symbol} or not found in Market Watch.")
            return {"success": False, "error": "Invalid symbol"}
            
        price = tick.ask if direction.upper() in ("BUY", "BULLISH") else tick.bid
        
        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            logger.error(f"Order failed: Could not get symbol info for {symbol}")
            return {"success": False, "error": "Symbol info error"}
            
        digits = sym_info.digits
        tick_size = sym_info.trade_tick_size
        
        # Round prices to the nearest valid tick step for the instrument
        if tick_size > 0:
            sl = round(round(sl / tick_size) * tick_size, digits) if sl > 0 else 0.0
            tp = round(round(tp / tick_size) * tick_size, digits) if tp > 0 else 0.0
        else:
            sl = round(sl, digits) if sl > 0 else 0.0
            tp = round(tp, digits) if tp > 0 else 0.0
        
        vol_step = sym_info.volume_step
        if vol_step > 0:
            volume = round(volume / vol_step) * vol_step
        volume = max(sym_info.volume_min, min(volume, sym_info.volume_max))
        volume = round(volume, 8)  # Clean float artifacts
        
        # Pre-execution validation to give clear error messages for stale signals (Invalid stops)
        stoplevel = sym_info.trade_stops_level * sym_info.point
        if direction.upper() in ("BUY", "BULLISH"):
            if sl > 0 and sl >= tick.bid - stoplevel:
                err_msg = (
                    f"Stale Signal (Slippage): SL ({sl}) is already hit or too close to current Bid ({tick.bid}). "
                    f"Stoplevel: {round(stoplevel, sym_info.digits)}"
                )
                logger.warning(err_msg)
                return {"success": False, "error": err_msg, "stale": True}
            if tp > 0 and tp <= tick.bid + stoplevel:
                err_msg = (
                    f"Stale Signal (Slippage): TP ({tp}) is already hit or too close to current Bid ({tick.bid}). "
                    f"Stoplevel: {round(stoplevel, sym_info.digits)}"
                )
                logger.warning(err_msg)
                return {"success": False, "error": err_msg, "stale": True}
        else:
            if sl > 0 and sl <= tick.ask + stoplevel:
                err_msg = (
                    f"Stale Signal (Slippage): SL ({sl}) is already hit or too close to current Ask ({tick.ask}). "
                    f"Stoplevel: {round(stoplevel, sym_info.digits)}"
                )
                logger.warning(err_msg)
                return {"success": False, "error": err_msg, "stale": True}
            if tp > 0 and tp >= tick.ask - stoplevel:
                err_msg = (
                    f"Stale Signal (Slippage): TP ({tp}) is already hit or too close to current Ask ({tick.ask}). "
                    f"Stoplevel: {round(stoplevel, sym_info.digits)}"
                )
                logger.warning(err_msg)
                return {"success": False, "error": err_msg, "stale": True}
        
        filling_type = mt5.ORDER_FILLING_FOK if (sym_info.filling_mode & 1) else mt5.ORDER_FILLING_IOC
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": action,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _executor,
            lambda: mt5.order_send(request)
        )

        if result is None:
            logger.error("Order failed: MT5 returned None (connection lost?)")
            return {"success": False, "error": "MT5 returned None"}

        # Phase-5 fix: fall back to ORDER_FILLING_RETURN once if the broker
        # rejects our bitmask-selected filling mode as unsupported (retcode
        # 10030 = TRADE_RETCODE_INVALID_FILL). Kept simple — one retry, no
        # further fallback chain.
        if result.retcode == 10030 and filling_type != mt5.ORDER_FILLING_RETURN:
            logger.warning(
                f"Order rejected: unsupported filling mode ({filling_type}) for {symbol} — "
                f"retrying once with ORDER_FILLING_RETURN."
            )
            retry_request = dict(request)
            retry_request["type_filling"] = mt5.ORDER_FILLING_RETURN
            result = await loop.run_in_executor(
                _executor,
                lambda: mt5.order_send(retry_request)
            )
            if result is None:
                logger.error("Order failed: MT5 returned None on filling-mode retry")
                return {"success": False, "error": "MT5 returned None"}

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            err = result.comment
            if result.retcode == 10016:
                err = f"Invalid stops | SL: {sl}, TP: {tp} | Ask: {tick.ask}, Bid: {tick.bid} | Stoplevel: {sym_info.trade_stops_level}"
            permanent_hint = _PERMANENT_RETCODES.get(result.retcode)
            if permanent_hint:
                err = f"{err} — {permanent_hint}"
                logger.error(f"Order failed: {result.retcode} - {err}")
                return {"success": False, "error": err, "permanent": True}
            logger.error(f"Order failed: {result.retcode} - {err}")
            return {"success": False, "error": err}

        return {"success": True, "ticket": result.order}

    @staticmethod
    async def place_multi_position_order(
        symbol: str,
        direction: str,
        volumes: list[float],
        sl: float,
        tps: list[float],
        magic_base: int
    ) -> list[dict[str, Any]]:
        """
        Place multiple sub-positions for TP1, TP2, TP3.
        """
        logger.info(f"Placing multi-position on {symbol}")
        results = []
        
        for i, (vol, tp) in enumerate(zip(volumes, tps)):
            magic = magic_base + i
            comment = f"TP{i+1}"
            res = await OrderManager.place_market_order(
                symbol, direction, vol, sl, tp, magic, comment
            )
            results.append(res)
            
        return results

    @staticmethod
    async def modify_sl(ticket: int, new_sl: float) -> bool:
        """Modify stop loss of an open position (used for BE and Trailing)."""
        logger.info(f"Modifying SL for {ticket} to {new_sl}")
        
        if not mt5:
            return True
            
        loop = asyncio.get_running_loop()
        position = await loop.run_in_executor(_executor, lambda: mt5.positions_get(ticket=ticket))
        if not position:
            return False
            
        pos = position[0]
        sym_info = mt5.symbol_info(pos.symbol)
        digits = sym_info.digits if sym_info else 5
        tick_size = sym_info.trade_tick_size if sym_info else 0.0
        point = sym_info.point if sym_info else 0.00001
        
        # Ensure SL respects minimum distance (stops level or spread)
        stops_level = sym_info.trade_stops_level if sym_info else 0
        spread = sym_info.spread if sym_info else 0
        min_distance = max(stops_level, spread, 2) * point

        tick = mt5.symbol_info_tick(pos.symbol)
        if tick:
            is_buy = pos.type == mt5.POSITION_TYPE_BUY
            if is_buy:
                max_sl = tick.bid - min_distance
                if new_sl > max_sl:
                    logger.info(
                        f"SL {new_sl:.5f} too close to bid {tick.bid:.5f} "
                        f"(stops_level={stops_level}, min_distance={min_distance:.5f}) "
                        f"for BUY {ticket} — adjusting to {max_sl:.5f}"
                    )
                    new_sl = max_sl
            else:
                min_sl = tick.ask + min_distance
                if new_sl < min_sl:
                    logger.info(
                        f"SL {new_sl:.5f} too close to ask {tick.ask:.5f} "
                        f"(stops_level={stops_level}, min_distance={min_distance:.5f}) "
                        f"for SELL {ticket} — adjusting to {min_sl:.5f}"
                    )
                    new_sl = min_sl

        # Skip if new_sl is not better than current SL
        if pos.sl != 0.0:
            is_buy = pos.type == mt5.POSITION_TYPE_BUY
            if (is_buy and new_sl <= pos.sl) or (not is_buy and new_sl >= pos.sl):
                logger.info(f"Skipping SL modification for {ticket}: adjusted SL {new_sl} is not better than current SL {pos.sl}")
                return True

        if tick_size > 0:
            rounded_sl = round(round(new_sl / tick_size) * tick_size, digits)
        else:
            rounded_sl = round(new_sl, digits)

        if rounded_sl == pos.sl:
            return True

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": rounded_sl,
            "tp": pos.tp,
            "magic": pos.magic
        }

        result = await loop.run_in_executor(
            _executor,
            lambda: mt5.order_send(request)
        )

        if result is None:
            logger.error("Modify SL failed: MT5 returned None")
            return False

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            # "Invalid stops" = price moved too fast, SL now too close — not a system error
            if "invalid stops" in (result.comment or "").lower():
                logger.info(
                    f"SL modify skipped for {ticket}: {result.comment} "
                    f"(requested={rounded_sl:.5f}, current_sl={pos.sl:.5f}) — price not far enough from entry yet"
                )
                return True  # Not an error — will retry next tick
            logger.error(f"Modify SL failed: {result.comment} (ticket={ticket}, sl={rounded_sl})")
            return False

        return True

    @staticmethod
    async def close_position(ticket: int) -> bool:
        """Close an open position at market."""
        logger.info(f"Closing position {ticket}")
        
        if not mt5:
            return True
            
        loop = asyncio.get_running_loop()
        position = await loop.run_in_executor(_executor, lambda: mt5.positions_get(ticket=ticket))
        if not position:
            logger.error(f"Position {ticket} not found")
            return False
            
        position = position[0]
        symbol = position.symbol
        # Phase-5 fix: position.type is a POSITION_TYPE_* value (0=BUY, 1=SELL),
        # not an ORDER_TYPE_* value — comparing against mt5.ORDER_TYPE_BUY only
        # "worked" because the two enums happen to share the same integer value
        # today; fragile to a future MT5 SDK change. Compare against the correct
        # POSITION_TYPE_BUY constant instead.
        direction = "SELL" if position.type == mt5.POSITION_TYPE_BUY else "BUY" # opposite to close
        action = mt5.ORDER_TYPE_SELL if direction == "SELL" else mt5.ORDER_TYPE_BUY
        
        tick = await loop.run_in_executor(_executor, lambda: mt5.symbol_info_tick(symbol))
        if not tick:
            logger.error(f"Close failed: Invalid symbol {symbol} or not found in Market Watch.")
            return False
            
        price = tick.bid if action == mt5.ORDER_TYPE_SELL else tick.ask
        
        sym_info = await loop.run_in_executor(_executor, lambda: mt5.symbol_info(symbol))
        filling_type = mt5.ORDER_FILLING_FOK if sym_info and (sym_info.filling_mode & 1) else mt5.ORDER_FILLING_IOC
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": position.volume,
            "type": action,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": position.magic,
            "comment": "manual close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        
        result = await loop.run_in_executor(
            _executor, 
            lambda: mt5.order_send(request)
        )
        if result is None:
            logger.error(f"Close position {ticket} failed: MT5 returned None")
            return False
            
        return result.retcode == mt5.TRADE_RETCODE_DONE

    @staticmethod
    async def get_closed_positions_since(last_check_time: float) -> list[dict[str, Any]]:
        """Get positions closed since last_check_time."""
        if not mt5:
            return []
            
        from datetime import datetime, timedelta
        
        loop = asyncio.get_running_loop()
        now = datetime.now() + timedelta(days=1)
        start_dt = datetime.fromtimestamp(last_check_time)
        
        deals = await loop.run_in_executor(
            _executor,
            lambda: mt5.history_deals_get(start_dt, now)
        )
        
        if not deals:
            return []
            
        closed_deals = []
        for d in deals:
            if d.entry == mt5.DEAL_ENTRY_OUT:
                closed_deals.append({
                    "ticket": d.ticket,
                    "position_id": d.position_id,
                    "symbol": d.symbol,
                    "profit": d.profit,
                    "commission": d.commission,
                    "swap": d.swap,
                    "time": d.time,
                    "price": d.price,
                    "reason": getattr(d, 'reason', -1),
                })
        return closed_deals

    @staticmethod
    async def get_balance_operations_since(last_check_time: float) -> list[dict[str, Any]]:
        """Get deposits, withdrawals, and other balance operations."""
        if not mt5:
            return []
            
        from datetime import datetime
        
        loop = asyncio.get_running_loop()
        now = datetime.now()
        start_dt = datetime.fromtimestamp(last_check_time)
        
        deals = await loop.run_in_executor(
            _executor,
            lambda: mt5.history_deals_get(start_dt, now)
        )
        
        if not deals:
            return []
            
        operations = []
        for d in deals:
            # 2=BALANCE, 3=CREDIT, 4=CHARGE, 5=CORRECTION, 6=BONUS, 7=COMMISSION
            if getattr(d, 'type', 0) >= 2:
                operations.append({
                    "ticket": getattr(d, 'ticket', 0),
                    "type": getattr(d, 'type', 2),
                    "profit": getattr(d, 'profit', 0.0), # For deposits this is positive, withdrawals negative
                    "comment": getattr(d, 'comment', ''),
                    "time": getattr(d, 'time', 0)
                })
        return operations
