"""
backend/groups/templates.py

Every signal and result message the group bot posts. Fixed templates - the
LLM never writes a signal or a result.

Rule: no source. Nothing here names the strategy, the copy-trading channel or
the account; members see one branded signal.
"""
from __future__ import annotations

import html
from typing import Any

DISPLAY = {
    "XAUUSD": "GOLD (XAUUSD)", "XAUUSDM": "GOLD (XAUUSD)", "GOLD": "GOLD (XAUUSD)",
    "US_TECH_100": "NASDAQ (US100)", "US TECH 100": "NASDAQ (US100)", "NAS100": "NASDAQ (US100)",
    "USTEC": "NASDAQ (US100)", "NDX100": "NASDAQ (US100)", "US100": "NASDAQ (US100)",
    "US_SP_500": "S&P 500 (US500)", "US SP 500": "S&P 500 (US500)", "SPX500": "S&P 500 (US500)", "US500": "S&P 500 (US500)",
    "WALL_STREET_30": "DOW (US30)", "WALL STREET 30": "DOW (US30)", "US30": "DOW (US30)",
    "XAGUSD": "SILVER (XAGUSD)", "BTCUSD": "BITCOIN (BTCUSD)", "USOIL": "US OIL (USOIL)",
}


def e(x: Any) -> str:
    return html.escape(str(x if x is not None else ""), quote=False)


def name(symbol: str) -> str:
    s = (symbol or "").upper().replace("-", "_")
    s = s[:-1] if s.endswith("M") and s[:-1] in DISPLAY else s
    return DISPLAY.get(s, DISPLAY.get(s.replace("_", " "), (symbol or "").upper().replace("_", " ")))


def digits_for(price: float | None) -> int:
    if price is None:
        return 2
    p = abs(float(price))
    if p < 20:
        return 5
    if p < 200:
        return 3
    return 2


def px(price: float | None, d: int | None = None) -> str:
    if price is None:
        return "-"
    d = digits_for(price) if d is None else d
    return f"{float(price):,.{d}f}"


def r_of(side: str, entry: float | None, sl: float | None, price: float | None) -> float | None:
    if None in (entry, sl, price) or entry == sl:
        return None
    s = 1 if side == "BUY" else -1
    return (float(price) - float(entry)) * s / abs(float(entry) - float(sl))


def fmt_r(r: float | None) -> str:
    return "" if r is None else f"{r:+.1f}R"


def _icon(side: str) -> str:
    return "🟢" if side == "BUY" else "🔴"


def status_line(status: str) -> str:
    return f"<i>Status: {e(status)}</i>"


# ── the signal thread ─────────────────────────────────────────────────────────

def headsup(symbol: str, side: str, level: float, cross: str, stop: float | None, limit_hint: bool = True) -> str:
    lines = [f"👀 <b>Heads-up: {e(name(symbol))} {e(side)} setup forming</b>",
             f"Entry trigger: a close {'above' if cross == 'above' else 'below'} <b>{px(level)}</b>"]
    if stop:
        lines.append(f"Stop: {px(stop)}")
    if limit_hint:
        order = ("buy" if side == "BUY" else "sell") + (" limit" if (side == "BUY") == (cross == "below") else " stop")
        lines.append(f"Ready early? Place a {order} at {px(level)}, or wait for the entry post.")
    return "\n".join(lines)


def entry(symbol: str, side: str, price: float | None, sl: float | None, tps: list[float], *,
          zone: tuple[float, float] | None = None, chase_limit: float | None = None, limit_order: bool = False) -> str:
    title = f"{_icon(side)} <b>{e(name(symbol))} {e(side)}</b>"
    lines = [title]
    if zone and zone[0] != zone[1]:
        lines.append(f"Entry zone: <b>{px(min(zone))} - {px(max(zone))}</b>")
    elif price is not None:
        lines.append(f"Entry: <b>{px(price)}</b>" + (" (limit)" if limit_order else ""))
    lines.append(f"SL: {px(sl)}")
    for i, tp in enumerate(tps[:5], start=1):
        r = r_of(side, price if price is not None else (sum(zone) / 2 if zone else None), sl, tp)
        lines.append(f"TP{i}: {px(tp)}" + (f"  ({fmt_r(r)})" if r is not None else ""))
    if limit_order:
        lines.append("Place it as a limit order at the entry price.")
    elif chase_limit is not None:
        lines.append(f"No limit placed? Enter at market now, up to {px(chase_limit)}. "
                     f"Past that, don't chase - skip this one.")
    lines.append("Risk 1-2% of your balance. Lot size: see the pinned calculator.")
    return "\n".join(lines)


def in_zone(symbol: str, side: str, zone: tuple[float, float]) -> str:
    lo, hi = min(zone), max(zone)
    limit = hi if side == "BUY" else lo
    return (f"📍 {e(name(symbol))}: price is inside the zone {px(lo)} - {px(hi)}. Limits are filling; "
            f"stay in the zone, don't {'buy above' if side == 'BUY' else 'sell below'} {px(limit)}.")


def tp_hit(n: int, price: float | None, r: float | None, *, move_be: bool = False, last: bool = False) -> str:
    s = f"🎯 TP{n} hit at {px(price)}" + (f" ({fmt_r(r)})" if r is not None else "") + "."
    if last:
        s += " All targets done."
    elif move_be:
        s += " Move your stop to entry - this trade is now risk-free."
    return s


def breakeven() -> str:
    return "🛡 Stop moved to entry. This trade can no longer lose."


def stop_moved(price: float | None) -> str:
    return f"🛡 Stop moved to {px(price)}."


def closed(result_r: float | None, how: str) -> str:
    if result_r is None:
        return f"🏁 Trade closed ({e(how)})."
    if result_r > 0.05:
        return f"✅ Closed in profit: <b>{fmt_r(result_r)}</b> ({e(how)})."
    if result_r < -0.05:
        return f"❌ Stopped out: <b>{fmt_r(result_r)}</b>. Part of the plan - risk was fixed before entry."
    return f"⚪ Closed at breakeven ({e(how)})."


def cancelled(symbol: str, why: str) -> str:
    return f"🚫 {e(name(symbol))} setup cancelled: {e(why)}. Delete any pending order for it."


def withdrawn(symbol: str, side: str) -> str:
    return f"↩️ {e(name(symbol))} {e(side)} heads-up withdrawn: the setup didn't complete. Delete any pending order for it."


# ── daily posts (template parts; the LLM may add one line) ────────────────────

def day_summary(rows: list[dict], day_label: str) -> str:
    if not rows:
        return f"📊 <b>{e(day_label)}</b>\nNo signals today. Patience is a position too."
    lines = [f"📊 <b>Today's signals - {e(day_label)}</b>"]
    total = 0.0
    wins = losses = 0
    for r in rows:
        res = r.get("result_r")
        tag = "running" if res is None and r.get("status") == "OPEN" else (fmt_r(res) if res is not None else e(r.get("status", "")).lower())
        lines.append(f"{_icon(r['side'])} {e(name(r['symbol']))} {e(r['side'])} - {tag}")
        if res is not None:
            total += res
            wins += res > 0.05
            losses += res < -0.05
    lines.append(f"<b>Closed: {wins} won, {losses} lost, net {fmt_r(total) or '0.0R'}</b>")
    return "\n".join(lines)


def week_summary(rows: list[dict], label: str) -> str:
    closed_rows = [r for r in rows if r.get("result_r") is not None]
    total = sum(r["result_r"] for r in closed_rows)
    wins = sum(1 for r in closed_rows if r["result_r"] > 0.05)
    best = max(closed_rows, key=lambda r: r["result_r"], default=None)
    lines = [f"🗓 <b>Week in numbers - {e(label)}</b>",
             f"Signals: {len(rows)} · closed: {len(closed_rows)} · won: {wins}",
             f"Net: <b>{fmt_r(total) or '0.0R'}</b>"]
    if best:
        lines.append(f"Best: {e(name(best['symbol']))} {e(best['side'])} {fmt_r(best['result_r'])}")
    return "\n".join(lines)


RISK_PIN = ("📌 <b>Read me first</b>\n"
            "• Trading carries risk. Past results don't promise future ones.\n"
            "• Risk 1-2% of your balance per signal. Never more than you can afford to lose.\n"
            "• Every signal gives entry, stop and targets; every result is posted, wins and losses.\n"
            "• Lot size = (balance × risk %) ÷ (stop distance × value per point).\n"
            "• Questions about money, payments or your account: message the admin directly.")
