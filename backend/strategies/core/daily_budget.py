"""
backend/strategies/core/daily_budget.py

The strategy-level daily risk budget (`max_daily_risk_pct`) shared by the
synthetic-index engines - SpikeFade / RangeRevert / RangeBreakout / TrendDrift
(strategy_synth), BoomDriftJump (strategy_boom) and DriftJumpAlpha
(strategy_two).

THE BUG THIS FIXES (live account, 2026-10-19)
---------------------------------------------
Each of those engines checked `used + risk_per_trade <= max_daily_risk_pct`.
`max_daily_risk_pct` defaults to 4.0, and the live account risks 5% per trade,
so `0.0% + 5.0% <= 4.0%` was false on the FIRST signal of every day: every
synthetic slot on the live account was blocked from ever trading, while demo
(at 1-1.8% per trade) traded normally. The live log shows it verbatim:
"daily risk cap reached (0.0% used + 5.0% > 4.0% allowed)".

A budget smaller than one trade is a configuration that means "at most one
trade a day", not "never trade": the account-level risk engine (daily and
weekly drawdown, hard cap) is what protects the account. So the first trade of
a day is always allowed, and the misconfiguration is reported once.
"""

from __future__ import annotations

from backend.utils.logger import get_logger

logger = get_logger(__name__)

_WARNED: set[tuple[str, str, float, float]] = set()


def allows(used_pct: float, risk_pct: float, max_daily_pct: float, *,
           strategy_id: str = "", symbol: str = "") -> bool:
    """May one more trade of `risk_pct` be taken after `used_pct` today?"""
    used, risk, cap = float(used_pct or 0.0), float(risk_pct or 0.0), float(max_daily_pct or 0.0)
    if cap <= 0:
        return False                    # a zero budget is "no entries" - it always was
    if risk > cap:
        key = (strategy_id, symbol, risk, cap)
        if key not in _WARNED:
            _WARNED.add(key)
            logger.warning(
                f"[{strategy_id}] {symbol}: max_daily_risk_pct {cap:g}% is smaller than ONE "
                f"trade's risk ({risk:g}% per trade) - this allows one trade a day. Raise "
                f"max_daily_risk_pct (Settings > Strategy) or lower the risk per trade.")
    if used <= 1e-9:
        return True                     # the first trade of the day is never blocked by the budget
    return used + risk <= cap + 1e-9
