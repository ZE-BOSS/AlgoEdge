"""Human labels for why a position closed, used by Telegram alerts.

A broker "SL" deal only says the stop was touched. Whether that stop was the
original loss-taking stop, a breakeven stop or a trailed stop in profit is only
visible from the money, so the label is chosen from both. Before this existed a
trailed stop that banked +$33 arrived as "Stop Loss Hit".
"""

from __future__ import annotations

# Strategy-initiated exits (TradeAction close_reason) worth naming plainly.
_STRATEGY_EXITS = {
    "RQ_SESSION_CLOSE": "Session Close (strategy rule)",
    "RQ_DAY_CHANGE": "Day Change Close (strategy rule)",
    "RQ_BAND_FLIP": "Opposite Signal Close (strategy rule)",
    "TIME_LIMIT": "Time Stop",
    "CLIENT": "Manual Close",
    "CLOSED": "Closed",
}

# A result this close to zero is a breakeven stop, not a win or a loss.
BREAKEVEN_TOLERANCE = 0.50


def close_reason_label(reason: str | None, net_profit: float | None) -> str:
    """Telegram title for a closed position."""
    r = (reason or "CLOSED").upper()
    pnl = float(net_profit or 0.0)
    if r in ("SL", "TRAIL"):
        if abs(pnl) <= BREAKEVEN_TOLERANCE:
            return "Breakeven Stop Hit"
        if pnl > 0:
            return "Trailing Stop Hit (profit)" if r == "TRAIL" else "Stop Hit in Profit"
        return "Stop Loss Hit"
    if r.startswith("TP"):
        level = r[2:]
        return f"Take Profit {level} Hit" if level.isdigit() else "Take Profit Hit"
    if r in _STRATEGY_EXITS:
        return _STRATEGY_EXITS[r]
    return r.replace("_", " ").title()
