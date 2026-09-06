"""
backend/api/routes/mt5_test.py

Endpoints for manual MT5 diagnostic testing from the frontend.
"""

import asyncio
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.api.deps import get_current_user
from backend.data.models import User
from backend.mt5.order_manager import OrderManager
from backend.utils.logger import get_logger
from backend.mt5.executor import mt5_executor

logger = get_logger(__name__)
router = APIRouter(prefix="/api/mt5_test", tags=["mt5_test"])

class EntryRequest(BaseModel):
    symbol: str
    direction: str

class TicketRequest(BaseModel):
    ticket: int

@router.post("/entry")
async def test_mt5_entry(req: EntryRequest, current_user: User = Depends(get_current_user)):
    try:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            mt5 = None
        from backend.brokers.factory import broker_factory
        broker = broker_factory.get_broker()
        if not mt5 or not broker.account_info:
            return {"success": False, "error": "MT5 is offline or not connected"}
            
        # Get user config to determine risk
        import json

        from sqlalchemy import select

        from backend.data.database import async_session
        from backend.data.models import UserConfigModel
        
        async with async_session() as session:
            result = await session.execute(select(UserConfigModel).where(UserConfigModel.user_id == current_user.id))
            config = result.scalar_one_or_none()
            if not config:
                return {"success": False, "error": "No config found for risk calculations"}
            config_data = json.loads(config.config_json)
            
        risk_pct = config_data.get("risk", {}).get("risk_per_trade_pct", 2.0)
        balance = broker.account_info.balance
        risk_dollar = balance * (risk_pct / 100.0)
        
        loop = asyncio.get_running_loop()
        tick = await loop.run_in_executor(mt5_executor, lambda: mt5.symbol_info_tick(req.symbol))
        sym_info = await loop.run_in_executor(mt5_executor, lambda: mt5.symbol_info(req.symbol))
        
        if not tick or not sym_info:
            return {"success": False, "error": f"Symbol {req.symbol} not found in Market Watch."}
            
        # Hardcode a 15 point SL distance for this test
        sl_points = 15.0
        
        # Calculate volume based on typical Deriv indices
        try:
            contract_size = sym_info.trade_contract_size if sym_info.trade_contract_size else 1.0
            raw_volume = risk_dollar / (sl_points * contract_size)
            # Round to step
            step = sym_info.volume_step if sym_info.volume_step else 0.01
            volume = max(sym_info.volume_min, round(raw_volume / step) * step)
            volume = min(volume, sym_info.volume_max)
        except Exception as e:
            logger.error(f"Volume calc error: {e}")
            volume = 0.20 # Fallback for Deriv
            
        price = tick.ask if req.direction.upper() == "BUY" else tick.bid
        sl = price - sl_points if req.direction.upper() == "BUY" else price + sl_points
        tp = price + sl_points * 2 if req.direction.upper() == "BUY" else price - sl_points * 2
        
        # Magic number
        magic = int(time.time()) % 1000000
        
        result = await OrderManager.place_market_order(
            symbol=req.symbol,
            direction=req.direction,
            volume=volume,
            sl=sl,
            tp=tp,
            magic=magic,
            comment="manual_test"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"MT5 Test Entry Error: {e}")
        return {"success": False, "error": str(e)}


@router.post("/close")
async def test_mt5_close(req: TicketRequest, current_user: User = Depends(get_current_user)):
    try:
        success = await OrderManager.close_position(req.ticket)
        if success:
            return {"success": True}
        return {"success": False, "error": "Failed to close position. Check MT5 logs."}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/breakeven")
async def test_mt5_breakeven(req: TicketRequest, current_user: User = Depends(get_current_user)):
    try:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            mt5 = None
        loop = asyncio.get_running_loop()
        position = await loop.run_in_executor(mt5_executor, lambda: mt5.positions_get(ticket=req.ticket))
        if not position:
            return {"success": False, "error": "Position not found"}
            
        price_open = position[0].price_open
        success = await OrderManager.modify_sl(req.ticket, price_open)
        if success:
            return {"success": True, "new_sl": price_open}
        return {"success": False, "error": "Failed to modify SL to breakeven"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/trail")
async def test_mt5_trail(req: TicketRequest, current_user: User = Depends(get_current_user)):
    try:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            mt5 = None
        loop = asyncio.get_running_loop()
        position = await loop.run_in_executor(mt5_executor, lambda: mt5.positions_get(ticket=req.ticket))
        if not position:
            return {"success": False, "error": "Position not found"}
            
        p = position[0]
        direction = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
        
        tick = await loop.run_in_executor(mt5_executor, lambda: mt5.symbol_info_tick(p.symbol))
        if not tick:
             return {"success": False, "error": "Tick not found"}
             
        # Trail by 5 points
        trail_points = 5.0
        
        if direction == "BUY":
            new_sl = tick.bid - trail_points
            if p.sl > 0 and new_sl <= p.sl:
                return {"success": False, "error": "New SL is worse than current SL"}
        else:
            new_sl = tick.ask + trail_points
            if p.sl > 0 and new_sl >= p.sl:
                return {"success": False, "error": "New SL is worse than current SL"}
                
        success = await OrderManager.modify_sl(req.ticket, new_sl)
        if success:
            return {"success": True, "new_sl": new_sl}
        return {"success": False, "error": "Failed to modify SL for trail"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Spread / Commission / Cost Auto-Fetch for Backtester
# ─────────────────────────────────────────────────────────────────────────────

def _get_pip_size_for_symbol(symbol: str) -> float:
    """Return pip size for a symbol. Forex 5-digit = 0.0001, JPY = 0.01, indices = 1.0."""
    sym_upper = symbol.upper()
    if "JPY" in sym_upper or "XAU" in sym_upper:
        return 0.01
    if any(idx in sym_upper for idx in ["US30", "US500", "NAS100", "NDX100",
                                         "UK100", "DE40", "JP225", "SPX", "DJI"]):
        return 1.0
    return 0.0001  # Standard 5-digit forex


@router.get("/symbol-costs/{symbol}")
async def get_symbol_costs(symbol: str, current_user: User = Depends(get_current_user)):
    """
    Returns current spread, commission, and swap data for a symbol from MT5.
    Used by the Backtester UI to auto-fill Simulation Costs fields.
    """
    try:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return {"success": False, "error": "MT5 not available"}

        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(mt5_executor, lambda: mt5.symbol_info(symbol))
        if info is None:
            return {"success": False, "error": f"Symbol {symbol} not found"}

        tick = await loop.run_in_executor(mt5_executor, lambda: mt5.symbol_info_tick(symbol))
        pip_size = _get_pip_size_for_symbol(symbol)

        # Current spread from tick data (more accurate than info.spread)
        current_spread_price = (tick.ask - tick.bid) if tick else 0
        current_spread_pips = current_spread_price / pip_size if pip_size else 0

        return {
            "success": True,
            "symbol": symbol,
            "spread_points": info.spread,
            "spread_pips": round(current_spread_pips, 2),
            "spread_price": round(current_spread_price, info.digits),
            "commission": getattr(info, 'trade_commission', 0.0),
            "swap_long": info.swap_long,
            "swap_short": info.swap_short,
            "digits": info.digits,
            "point": info.point,
            "trade_tick_size": info.trade_tick_size,
            "stops_level": info.trade_stops_level,
            "freeze_level": info.trade_freeze_level,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/average-spread/{symbol}")
async def get_average_spread(
    symbol: str,
    minutes: int = 60,
    current_user: User = Depends(get_current_user),
):
    """
    Compute average spread over recent ticks for a symbol.
    Falls back to symbol_info.spread if tick data is unavailable.
    """
    try:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return {"success": False, "error": "MT5 not available"}

        import numpy as np
        from datetime import datetime, timedelta, timezone

        loop = asyncio.get_running_loop()
        now = datetime.now(timezone.utc)
        from_time = now - timedelta(minutes=minutes)

        # Try tick data first
        ticks = await loop.run_in_executor(mt5_executor,
            lambda: mt5.copy_ticks_range(symbol, from_time, now, mt5.COPY_TICKS_INFO)
            if hasattr(mt5, 'copy_ticks_range') else None
        )

        pip_size = _get_pip_size_for_symbol(symbol)

        if ticks is not None and len(ticks) > 0:
            spreads = ticks["ask"] - ticks["bid"]
            return {
                "success": True,
                "symbol": symbol,
                "avg_spread_pips": round(float(spreads.mean()) / pip_size, 2),
                "max_spread_pips": round(float(spreads.max()) / pip_size, 2),
                "min_spread_pips": round(float(spreads.min()) / pip_size, 2),
                "p95_spread_pips": round(float(np.percentile(spreads, 95)) / pip_size, 2),
                "median_spread_pips": round(float(np.median(spreads)) / pip_size, 2),
                "sample_minutes": minutes,
                "tick_count": len(ticks),
                "source": "tick_data",
            }
        else:
            # Fallback: use symbol_info current spread
            info = await loop.run_in_executor(mt5_executor, lambda: mt5.symbol_info(symbol))
            if info:
                spread_pips = (info.spread * info.point) / pip_size if pip_size else 0
                return {
                    "success": True,
                    "symbol": symbol,
                    "avg_spread_pips": round(spread_pips, 2),
                    "max_spread_pips": round(spread_pips * 1.5, 2),  # Estimate
                    "min_spread_pips": round(spread_pips * 0.7, 2),  # Estimate
                    "p95_spread_pips": round(spread_pips * 1.3, 2),  # Estimate
                    "median_spread_pips": round(spread_pips, 2),
                    "sample_minutes": 0,
                    "tick_count": 0,
                    "source": "symbol_info_fallback",
                }
            return {"success": False, "error": f"No data available for {symbol}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
