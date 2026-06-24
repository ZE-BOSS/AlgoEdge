"""
backend/mt5/order_manager.py

Order placement, modification, and multi-position management logic.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List
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
    ) -> Dict[str, Any]:
        """Place a single market order."""
        logger.info(f"Placing {direction} on {symbol} (vol: {volume})")
        
        if not mt5:
            logger.info("MOCK MODE: Order placed successfully.")
            return {"success": True, "ticket": 12345}
            
        action = mt5.ORDER_TYPE_BUY if direction.upper() in ("BUY", "BULLISH") else mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(symbol).ask if direction.upper() in ("BUY", "BULLISH") else mt5.symbol_info_tick(symbol).bid
        
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
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor, 
            lambda: mt5.order_send(request)
        )
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result.retcode} - {result.comment}")
            return {"success": False, "error": result.comment}
            
        return {"success": True, "ticket": result.order}

    @staticmethod
    async def place_multi_position_order(
        symbol: str,
        direction: str,
        volumes: List[float],
        sl: float,
        tps: List[float],
        magic_base: int
    ) -> List[Dict[str, Any]]:
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
            
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": new_sl
        }
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor, 
            lambda: mt5.order_send(request)
        )
        
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
            
        position = mt5.positions_get(ticket=ticket)
        if not position:
            logger.error(f"Position {ticket} not found")
            return False
            
        position = position[0]
        symbol = position.symbol
        direction = "SELL" if position.type == mt5.ORDER_TYPE_BUY else "BUY" # opposite to close
        action = mt5.ORDER_TYPE_SELL if direction == "SELL" else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(symbol).bid if action == mt5.ORDER_TYPE_SELL else mt5.symbol_info_tick(symbol).ask
        
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
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor, 
            lambda: mt5.order_send(request)
        )
        
        return result.retcode == mt5.TRADE_RETCODE_DONE
