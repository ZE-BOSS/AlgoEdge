"""
backend/copytrade/mt5ops.py

The MT5 calls copy trading needs and the bot's OrderManager does not have:
pending (limit/stop) orders, moving or cancelling them, moving a stop in EITHER
direction (OrderManager.modify_sl only ever tightens - right for a trailing
stop, wrong when a channel says "widen the SL"), and closing part of a position.

Every call runs on the single MT5 thread (backend.mt5.executor). Requests use
exact Python types: the bridge and newer MetaTrader5 packages refuse floats
where ints are expected and say so only by returning None.

Each function returns (ok, detail, extra) so the executor can log precisely
what happened.
"""

from __future__ import annotations

import time
from typing import Any

from backend.mt5.executor import run_mt5
from backend.utils.logger import get_logger

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - tests patch this module's functions
    mt5 = None

logger = get_logger(__name__)


def _ok_retcode(res: Any) -> bool:
    return res is not None and res.retcode in (getattr(mt5, "TRADE_RETCODE_DONE", 10009),
                                               getattr(mt5, "TRADE_RETCODE_PLACED", 10008),
                                               getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010))


async def _send(request: dict) -> tuple[bool, str, Any]:
    if mt5 is None:
        return False, "MT5 is not available on this server", None
    res = await run_mt5(mt5.order_send, request)
    if res is None:
        why = await run_mt5(mt5.last_error)
        return False, f"MT5 returned None: {why}", None
    if not _ok_retcode(res):
        return False, f"{res.retcode} {res.comment}", res
    return True, f"{res.retcode} {res.comment}", res


def _round_price(v: float | None, spec: dict) -> float:
    if not v:
        return 0.0
    ts = spec.get("tick_size") or 0
    d = int(spec.get("digits") or 5)
    return round(round(v / ts) * ts, d) if ts > 0 else round(v, d)


def _filling(info: Any) -> int:
    fm = getattr(info, "filling_mode", 0) or 0
    if fm & 1:
        return int(mt5.ORDER_FILLING_FOK)
    if fm & 2:
        return int(mt5.ORDER_FILLING_IOC)
    return int(mt5.ORDER_FILLING_RETURN)


async def available_symbols() -> list[str]:
    if mt5 is None:
        return []
    syms = await run_mt5(mt5.symbols_get)
    return [s.name for s in (syms or [])]


async def symbol_spec(symbol: str) -> dict | None:
    if mt5 is None:
        return None
    await run_mt5(mt5.symbol_select, symbol, True)
    info = await run_mt5(mt5.symbol_info, symbol)
    if info is None:
        return None
    return {
        "point": info.point, "tick_size": info.trade_tick_size or info.point,
        "tick_value": info.trade_tick_value, "volume_min": info.volume_min,
        "volume_step": info.volume_step, "volume_max": info.volume_max,
        "digits": info.digits, "stops_level": info.trade_stops_level,
        "trade_mode": getattr(info, "trade_mode", 4), "_info": info,
    }


async def quote(symbol: str) -> dict | None:
    if mt5 is None:
        return None
    t = await run_mt5(mt5.symbol_info_tick, symbol)
    return {"bid": t.bid, "ask": t.ask, "time": t.time} if t else None


async def balance() -> float:
    if mt5 is None:
        return 0.0
    acc = await run_mt5(mt5.account_info)
    return float(acc.balance) if acc else 0.0


async def place(symbol: str, side: str, kind: str, volume: float, price: float, sl: float,
                tp: float | None, magic: int, comment: str, spec: dict,
                expiry_hours: float = 0) -> tuple[bool, str, int | None]:
    """kind MARKET / LIMIT / STOP. Returns (ok, detail, ticket)."""
    buy = side == "BUY"
    info = spec.get("_info")
    if kind == "MARKET":
        q = await quote(symbol)
        if not q:
            return False, f"no price for {symbol}", None
        px = q["ask"] if buy else q["bid"]
        req = {"action": int(mt5.TRADE_ACTION_DEAL),
               "type": int(mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL),
               "price": float(px), "deviation": 50,
               "type_filling": _filling(info)}
    else:
        otype = {("BUY", "LIMIT"): mt5.ORDER_TYPE_BUY_LIMIT, ("SELL", "LIMIT"): mt5.ORDER_TYPE_SELL_LIMIT,
                 ("BUY", "STOP"): mt5.ORDER_TYPE_BUY_STOP, ("SELL", "STOP"): mt5.ORDER_TYPE_SELL_STOP}[(side, kind)]
        req = {"action": int(mt5.TRADE_ACTION_PENDING), "type": int(otype),
               "price": float(_round_price(price, spec)),
               "type_filling": int(mt5.ORDER_FILLING_RETURN)}
        if expiry_hours and expiry_hours > 0:
            req["type_time"] = int(mt5.ORDER_TIME_SPECIFIED)
            req["expiration"] = int(time.time() + expiry_hours * 3600)
    req.update({"symbol": str(symbol), "volume": float(volume),
                "sl": float(_round_price(sl, spec)), "tp": float(_round_price(tp, spec)),
                "magic": int(magic), "comment": str(comment)[:31]})
    req.setdefault("type_time", int(mt5.ORDER_TIME_GTC))
    ok, detail, res = await _send(req)
    if not ok and res is not None and res.retcode == 10030 and kind == "MARKET":
        req["type_filling"] = int(mt5.ORDER_FILLING_RETURN)
        ok, detail, res = await _send(req)
    if not ok and res is not None and res.retcode == 10022 and "expiration" in req:
        # broker refuses expiry times: place GTC, the executor cancels it on time
        req.pop("expiration")
        req["type_time"] = int(mt5.ORDER_TIME_GTC)
        ok, detail, res = await _send(req)
    return ok, detail, (int(res.order) if ok and res is not None else None)


async def get_position(ticket: int) -> Any | None:
    if mt5 is None:
        return None
    r = await run_mt5(mt5.positions_get, ticket=int(ticket))
    return r[0] if r else None


async def get_order(ticket: int) -> Any | None:
    if mt5 is None:
        return None
    r = await run_mt5(mt5.orders_get, ticket=int(ticket))
    return r[0] if r else None


async def modify_position(ticket: int, sl: float | None = None, tp: float | None = None,
                          spec: dict | None = None) -> tuple[bool, str]:
    pos = await get_position(ticket)
    if pos is None:
        return False, f"position {ticket} is not open"
    spec = spec or await symbol_spec(pos.symbol) or {}
    new_sl = _round_price(sl, spec) if sl is not None else pos.sl
    new_tp = _round_price(tp, spec) if tp is not None else pos.tp
    if new_sl == pos.sl and new_tp == pos.tp:
        return True, "unchanged"
    ok, detail, _ = await _send({"action": int(mt5.TRADE_ACTION_SLTP), "position": int(ticket),
                                 "symbol": str(pos.symbol), "sl": float(new_sl), "tp": float(new_tp),
                                 "magic": int(pos.magic)})
    return ok, detail


async def modify_order(ticket: int, price: float | None = None, sl: float | None = None,
                       tp: float | None = None, spec: dict | None = None) -> tuple[bool, str]:
    o = await get_order(ticket)
    if o is None:
        return False, f"pending order {ticket} not found"
    spec = spec or await symbol_spec(o.symbol) or {}
    req = {"action": int(mt5.TRADE_ACTION_MODIFY), "order": int(ticket), "symbol": str(o.symbol),
           "price": float(_round_price(price, spec) if price is not None else o.price_open),
           "sl": float(_round_price(sl, spec) if sl is not None else o.sl),
           "tp": float(_round_price(tp, spec) if tp is not None else o.tp),
           "type_time": int(o.type_time), "expiration": int(o.time_expiration or 0)}
    ok, detail, _ = await _send(req)
    return ok, detail


async def cancel_order(ticket: int) -> tuple[bool, str]:
    ok, detail, _ = await _send({"action": int(mt5.TRADE_ACTION_REMOVE), "order": int(ticket)})
    return ok, detail


async def close_volume(ticket: int, volume: float | None = None) -> tuple[bool, str]:
    """Close `volume` lots of a position (all of it when None)."""
    pos = await get_position(ticket)
    if pos is None:
        return False, f"position {ticket} is not open"
    spec = await symbol_spec(pos.symbol) or {}
    vol = pos.volume if volume is None else min(pos.volume, volume)
    step = spec.get("volume_step") or 0.01
    vol = round(int(vol / step + 1e-9) * step, 8)
    if vol < (spec.get("volume_min") or 0.01) - 1e-12:
        return False, f"{vol} lots is below the minimum - cannot close part of {ticket}"
    q = await quote(pos.symbol)
    if not q:
        return False, f"no price for {pos.symbol}"
    buy = pos.type == mt5.POSITION_TYPE_BUY
    ok, detail, res = await _send({
        "action": int(mt5.TRADE_ACTION_DEAL), "symbol": str(pos.symbol), "volume": float(vol),
        "type": int(mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY), "position": int(ticket),
        "price": float(q["bid"] if buy else q["ask"]), "deviation": 50, "magic": int(pos.magic),
        "comment": "AE_CT close", "type_time": int(mt5.ORDER_TIME_GTC), "type_filling": _filling(spec.get("_info")),
    })
    if not ok and res is not None and res.retcode == 10030:
        ok, detail, res = await _send({**{
            "action": int(mt5.TRADE_ACTION_DEAL), "symbol": str(pos.symbol), "volume": float(vol),
            "type": int(mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY), "position": int(ticket),
            "price": float(q["bid"] if buy else q["ask"]), "deviation": 50, "magic": int(pos.magic),
            "comment": "AE_CT close", "type_time": int(mt5.ORDER_TIME_GTC)},
            "type_filling": int(mt5.ORDER_FILLING_RETURN)})
    return ok, detail


async def ticket_state(ticket: int) -> str:
    """PENDING if still an order, OPEN if a position, else GONE (filled-and-closed, cancelled or expired)."""
    if await get_order(ticket) is not None:
        return "PENDING"
    if await get_position(ticket) is not None:
        return "OPEN"
    return "GONE"


async def closed_by_tp(ticket: int) -> bool:
    """Whether a position that is gone was closed by its take profit."""
    if mt5 is None:
        return False
    deals = await run_mt5(mt5.history_deals_get, position=int(ticket))
    for d in deals or []:
        if getattr(d, "entry", None) == getattr(mt5, "DEAL_ENTRY_OUT", 1) and \
                getattr(d, "reason", None) == getattr(mt5, "DEAL_REASON_TP", 5):
            return True
    return False
