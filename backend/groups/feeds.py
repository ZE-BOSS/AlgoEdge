"""
backend/groups/feeds.py

Small adapters from the trading code to the group router. Every function
swallows its own errors and schedules the router call, so the trading path
never waits on (or breaks because of) the group bot.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)


def active() -> bool:
    return bool(os.getenv("GROUP_BOT_TOKEN", "").strip()) and os.getenv("ALGOEDGE_AUDIT") != "1"


def _spawn(coro) -> None:
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()


# ── the bot's own strategy trades ─────────────────────────────────────────────

def strategy_opened(strategy_id: str, group_id: Any, signal: Any, legs: list[dict]) -> None:
    if not active() or not group_id:
        return
    try:
        from backend.groups.router import group_router
        tps = [float(p["tp_price"]) for p in legs if p.get("tp_price")]
        _spawn(group_router.on_open("strategy", str(strategy_id), f"grp:{group_id}", str(signal.symbol),
                                    str(signal.direction).upper(), float(signal.entry_price),
                                    float(signal.stop_loss), sorted(set(tps), key=lambda t: abs(t - float(signal.entry_price)))))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[GROUPS] strategy_opened: {e}")


def strategy_leg_closed(trade: Any, pos: Any, reason: str, exit_price: float | None) -> None:
    if not active() or trade is None or not getattr(trade, "group_id", None):
        return
    try:
        from backend.groups.router import group_router
        key = f"grp:{trade.group_id}"
        if str(reason).upper().startswith("TP") and getattr(pos, "tp_level", None):
            _spawn(group_router.on_event(key, {"type": "tp", "n": int(pos.tp_level), "price": exit_price}))
        if getattr(trade, "status", "") == "CLOSED":
            _spawn(group_router.on_event(key, {"type": "closed", "r": getattr(trade, "risk_reward", None),
                                               "price": exit_price, "how": _how(reason)}))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[GROUPS] strategy_leg_closed: {e}")


def strategy_breakeven(trade: Any) -> None:
    if not active() or trade is None or not getattr(trade, "group_id", None):
        return
    try:
        from backend.groups.router import group_router
        _spawn(group_router.on_event(f"grp:{trade.group_id}", {"type": "be"}))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[GROUPS] strategy_breakeven: {e}")


def headsup(strategy_id: str, slot_id: str, symbol: str, lv: dict) -> None:
    if not active():
        return
    try:
        from backend.groups.router import group_router
        _spawn(group_router.on_headsup("strategy", str(strategy_id), f"hu:{slot_id}", str(symbol), lv["direction"],
                                       float(lv["level"]), lv.get("cross") or "above", lv.get("stop")))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[GROUPS] headsup: {e}")


def headsup_withdrawn(slot_id: str) -> None:
    if not active():
        return
    try:
        from backend.groups.router import group_router
        _spawn(group_router.on_withdrawn(f"hu:{slot_id}"))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[GROUPS] withdrawn: {e}")


def _how(reason: str) -> str:
    r = str(reason or "").upper()
    if r.startswith("TP"):
        return "target hit"
    if r in ("SL",):
        return "stop hit"
    if r in ("TRAIL", "BE"):
        return "trailing stop"
    return "closed by the plan"


# ── copy trading ──────────────────────────────────────────────────────────────

def copier_message(sig: Any, ch: Any, orders: list[Any]) -> None:
    """A processed channel message: a new signal we took, or a follow-up we acted on."""
    if not active():
        return
    try:
        from backend.groups.router import group_router
        pd = dict(getattr(sig, "parsed", None) or {})
        kind, status = getattr(sig, "kind", ""), getattr(sig, "status", "")
        if kind == "OPEN" and status in ("EXECUTED", "PARTIAL"):
            sym = (orders[0].symbol if orders else None) or pd.get("symbol") or ""
            side = (pd.get("side") or "").upper()
            lo, hi = pd.get("entry_low"), pd.get("entry_high")
            zone = (float(lo), float(hi)) if lo and hi and lo != hi else None
            entry = pd.get("entry") or (orders[0].entry if orders else None)
            sl = next((o.sl for o in orders if getattr(o, "sl", None)), pd.get("sl"))
            tps = [float(t) for t in (pd.get("tps") or [])]
            _spawn(group_router.on_open("channel", str(ch.id), f"ct:{sig.id}", sym, side,
                                        float(entry) if entry else None, float(sl) if sl else None, tps,
                                        zone=zone, limit_order=(pd.get("order_type") == "LIMIT")))
            return
        root = getattr(sig, "root_signal_id", None)
        if not root or status != "EXECUTED":
            return
        key = f"ct:{root}"
        if kind == "BREAKEVEN":
            _spawn(group_router.on_event(key, {"type": "be"}))
        elif kind == "MOVE_SL" and pd.get("new_price"):
            _spawn(group_router.on_event(key, {"type": "sl_moved", "price": float(pd["new_price"])}))
        elif kind == "CANCEL":
            _spawn(group_router.on_event(key, {"type": "cancelled", "why": "the setup was called off"}))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[GROUPS] copier_message: {e}")


def copier_round(root: Any, rows: list[Any], snap: dict, closed: dict) -> None:
    """Order changes seen at the broker: a leg closing at its target, or the signal finishing."""
    if not active():
        return
    try:
        from backend.groups.router import group_router
        key = f"ct:{root.id}"
        exits = []
        for r in rows:
            prev = snap.get(r.id)
            c = closed.get(r.id) or {}
            px = c.get("price")
            if prev and prev[0] in ("OPEN",) and r.status == "CLOSED" and px is not None:
                exits.append(float(px))
                if r.tp and abs(float(px) - float(r.tp)) <= max(abs(float(r.tp)) * 0.0008, 1e-9):
                    _spawn(group_router.on_event(key, {"type": "tp", "n": int(r.leg or 1), "price": float(px)}))
        if getattr(root, "status", "") == "CLOSED":
            _spawn(group_router.on_event(key, {"type": "closed", "exits": exits, "how": "closed"}))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[GROUPS] copier_round: {e}")
