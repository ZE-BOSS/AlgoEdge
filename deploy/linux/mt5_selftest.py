"""
deploy/linux/mt5_selftest.py - does every MT5 call the backend makes work on
THIS server? Run through mt5_selftest.sh (it loads the instance's settings).

Part 1 (any instance, read-only): every MetaTrader5 function the backend uses,
called through the bridge in the SAME form the backend calls it, plus the app's
own data, account and history functions.

Part 2 (--trade, DEMO accounts only): one full trade through the code the bot
itself uses - OrderManager.place_market_order -> modify_sl -> close_position -
then the closed deal is looked up the ways the journal sync does. Minimum lot.
"""

from __future__ import annotations

import asyncio
import sys
import time
import traceback
from datetime import datetime, timedelta

import backend                                          # installs the bridge for this instance  # noqa: F401
import MetaTrader5 as mt5

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  - {detail}" if detail else ""), flush=True)
    return bool(ok)


def call(label: str, fn, ok=lambda r: r is not None, show=lambda r: ""):
    try:
        r = fn()
        good = ok(r)
        return check(label, good, show(r) if good else f"got {r!r:.120} last_error={mt5.last_error()}"), r
    except Exception as exc:                            # noqa: BLE001
        return check(label, False, f"{type(exc).__name__}: {exc}"), None


async def main(symbol: str, trade: bool) -> int:
    print(f"MetaTrader5 via bridge: {getattr(mt5, '__version__', '?')}")
    print("\n== Part 1: every MT5 call the app makes (read-only)")
    call("initialize()", lambda: mt5.initialize(), ok=bool)
    _, acc = call("account_info()", mt5.account_info, show=lambda a: f"{a.login} on {a.server}, balance {a.balance:,.2f}")
    call("terminal_info().connected", mt5.terminal_info, ok=lambda t: t is not None and t.connected,
         show=lambda t: f"trade_allowed={t.trade_allowed}")
    call(f"symbol_select({symbol!r}, True)", lambda: mt5.symbol_select(symbol, True), ok=bool)
    _, info = call(f"symbol_info({symbol!r})", lambda: mt5.symbol_info(symbol),
                   show=lambda i: f"volume_min={i.volume_min} digits={i.digits}")
    # a symbol just added to Market Watch has no price for a moment
    for _ in range(20):
        t = mt5.symbol_info_tick(symbol)
        if t is not None and t.ask > 0:
            break
        time.sleep(0.25)
    _, tick = call(f"symbol_info_tick({symbol!r})", lambda: mt5.symbol_info_tick(symbol),
                   ok=lambda t: t is not None and t.ask > 0, show=lambda t: f"bid={t.bid} ask={t.ask}")
    call("symbols_get()", mt5.symbols_get, ok=lambda s: s is not None and len(s) > 0, show=lambda s: f"{len(s)} symbols")
    now = datetime.now()
    call("copy_rates_from_pos(symbol, M5, 0, 100)", lambda: mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 100),
         ok=lambda r: r is not None and len(r) > 0, show=lambda r: f"{len(r)} bars")
    call("copy_rates_range(symbol, M15, from, to)",
         lambda: mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, now - timedelta(days=3), now),
         ok=lambda r: r is not None and len(r) > 0, show=lambda r: f"{len(r)} bars")
    call("copy_rates_from(symbol, H1, to, 50)", lambda: mt5.copy_rates_from(symbol, mt5.TIMEFRAME_H1, now, 50),
         ok=lambda r: r is not None and len(r) > 0, show=lambda r: f"{len(r)} bars")
    call("positions_get()", mt5.positions_get, show=lambda p: f"{len(p)} open")
    call("positions_get(symbol=...)", lambda: mt5.positions_get(symbol=symbol), show=lambda p: f"{len(p)} open")
    call("history_deals_get(from, to)  [journal sync, cash ledger]",
         lambda: mt5.history_deals_get(now - timedelta(days=30), now + timedelta(days=1)), show=lambda d: f"{len(d)} deals")
    call("history_deals_get(from, to, group=...)  [broker costs]",
         lambda: mt5.history_deals_get(now - timedelta(days=30), now + timedelta(days=1), group=f"*{symbol}*"),
         show=lambda d: f"{len(d)} deals")
    if tick is not None and info is not None:
        call("order_calc_margin(BUY, symbol, lots, price)  [position sizing]",
             lambda: mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, float(info.volume_min), float(tick.ask)),
             show=lambda m: f"{m:,.2f}")

    from backend.brokers.factory import broker_factory
    broker = broker_factory.get_broker()
    ok = await broker.ensure_connected()
    check("app broker: ensure_connected()", ok)
    a = await broker.get_live_account_info()
    check("app broker: get_live_account_info()  [risk sizing]", a is not None,
          f"balance {a.balance:,.2f}" if a else "None - the bot would refuse every trade")
    from backend.mt5.data_fetcher import DataFetcher
    try:
        df = await DataFetcher.get_historical_data(symbol, "M5", count=200)
        check("app DataFetcher.get_historical_data(M5, 200)", df is not None and len(df) > 0,
              f"{0 if df is None else len(df)} bars")
    except Exception as exc:                            # noqa: BLE001
        check("app DataFetcher.get_historical_data(M5, 200)", False, f"{type(exc).__name__}: {exc}")
    from backend.mt5.order_manager import OrderManager
    try:
        closed = await OrderManager.get_closed_positions_since(time.time() - 30 * 86400)
        check("app OrderManager.get_closed_positions_since(30 days)  [journal sync]", closed is not None,
              f"{len(closed)} closed")
    except Exception as exc:                            # noqa: BLE001
        check("app OrderManager.get_closed_positions_since(30 days)", False, f"{type(exc).__name__}: {exc}")

    if trade:
        print(f"\n== Part 2: one full trade through the bot's own order code ({symbol}, minimum lot)")
        demo = acc is not None and getattr(acc, "trade_mode", None) == getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        if not check("account is a DEMO account (trade test refuses otherwise)", demo,
                     f"trade_mode={getattr(acc, 'trade_mode', '?')}"):
            return summary()
        info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
        point = info.point or 10 ** -info.digits
        dist = max(info.trade_stops_level * point * 3, tick.ask * 0.01, 50 * point)
        sl, tp = tick.ask - dist, tick.ask + dist
        res = await OrderManager.place_market_order(symbol, "BUY", float(info.volume_min), sl, tp,
                                                    magic=999001, comment="AE_selftest")
        if not check("place_market_order  [bot entry]", res.get("success"), str(res)[:160]):
            return summary()
        ticket = int(res.get("ticket") or 0)
        await asyncio.sleep(1.5)
        pos = mt5.positions_get(ticket=ticket) or mt5.positions_get(symbol=symbol)
        pos = [p for p in (pos or ()) if p.magic == 999001]
        check("position visible: positions_get(ticket=...)", bool(pos), f"ticket {ticket}")
        if pos:
            ticket = pos[0].ticket
            new_sl = pos[0].sl + dist * 0.4
            ok = await OrderManager.modify_sl(ticket, new_sl)
            p2 = mt5.positions_get(ticket=ticket)
            check("modify_sl  [trailing / break-even]", ok and p2 and abs(p2[0].sl - new_sl) < dist * 0.2,
                  f"sl {pos[0].sl} -> {p2[0].sl if p2 else '?'}")
            ok = await OrderManager.close_position(ticket)
            check("close_position  [strategy exits, manual close]", ok, f"ticket {ticket}")
            await asyncio.sleep(2.0)
            check("position gone after close", not mt5.positions_get(ticket=ticket))
            deals = mt5.history_deals_get(position=ticket)
            check("history_deals_get(position=...)  [exit price / P&L]", bool(deals) and len(deals) >= 2,
                  f"{0 if not deals else len(deals)} deals")
            orders = mt5.history_orders_get(position=ticket)
            check("history_orders_get(position=...)", bool(orders), f"{0 if not orders else len(orders)} orders")
            closed = await OrderManager.get_closed_positions_since(time.time() - 600)
            mine = [c for c in (closed or []) if int(c.get("position_id") or c.get("ticket") or 0) == ticket
                    or c.get("magic") == 999001]
            check("closed trade found by get_closed_positions_since  [journal sync]", bool(mine),
                  f"{len(closed or [])} closed in the last 10 min")
    return summary()


def summary() -> int:
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n== {len(RESULTS) - len(bad)}/{len(RESULTS)} passed" + ("" if not bad else " - FAILED: " + "; ".join(r[0] for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    try:
        sys.exit(asyncio.run(main(args[0] if args else "Boom 900 Index", "--trade" in sys.argv)))
    except Exception:                                   # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
