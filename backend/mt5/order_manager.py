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
        filling_type = mt5.ORDER_FILLING_FOK if sym_info and (sym_info.filling_mode & 1) else mt5.ORDER_FILLING_IOC
        
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
            
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result.retcode} - {result.comment}")
            return {"success": False, "error": result.comment}
            
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
            
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": position[0].symbol,
            "sl": new_sl,
            "tp": position[0].tp,
            "magic": position[0].magic
        }
        
        result = await loop.run_in_executor(
            _executor, 
            lambda: mt5.order_send(request)
        )
        
        if result is None:
            logger.error("Modify SL failed: MT5 returned None")
            return False
            
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Modify SL failed: {result.comment}")
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
        direction = "SELL" if position.type == mt5.ORDER_TYPE_BUY else "BUY" # opposite to close
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
