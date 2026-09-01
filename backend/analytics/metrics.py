"""
backend/analytics/metrics.py

Trade and portfolio metrics computation.
Source: TradingBot_MasterPlan-2.md Section 8 — Trade Metrics Computation
Source: RiskManagement_Spec.md Section 8
"""

from typing import Any

import numpy as np

from backend.utils.logger import get_logger

logger = get_logger(__name__)


def calculate_pips(symbol: str, price1: float, price2: float) -> float:
    """Calculate pip distance between two prices."""
    from backend.risk.position_sizer import get_pip_size
    pip_size = get_pip_size(symbol)
    if pip_size == 0:
        return 0.0
    return abs(price1 - price2) / pip_size


def compute_trade_metrics(trade: dict[str, Any]) -> dict[str, Any]:
    """
    Compute per-trade metrics after a trade closes.
    Source: TradingBot_MasterPlan-2.md — compute_trade_metrics
    """
    entry = trade.get("entry_price", 0)
    exit_p = trade.get("exit_price", 0)
    direction = trade.get("direction", "BUY")
    symbol = trade.get("symbol", "")

    if direction == "BUY":
        pnl_raw = exit_p - entry
    else:
        pnl_raw = entry - exit_p

    # R MUST be measured against the ORIGINAL risk taken at entry, never against
    # the current stop. `trade["stop_loss"]` is MUTATED in place by break-even and
    # trailing logic, so by exit time it typically sits at (or near) entry.
    #
    # Using it as the denominator was a severe reporting bug: once break-even moved
    # the stop to entry the denominator collapsed toward zero and R exploded. Real
    # example from a run — SPX500, signal risk 67.02 pts, recorded stop distance at
    # exit 0.43 pts, reported R = 243.51 for a trade whose true R was 1.50 (a plain
    # TP1 hit). Pooled, this reported +14.87R expectancy on a run that LOST money.
    # Every R-denominated metric downstream (expectancy_r, avg_win_r, total_pnl_r)
    # was corrupted by it.
    #
    # Resolution order: an explicitly recorded initial stop, then the untouched
    # signal that opened the trade, and only then the live stop as a last resort.
    original_signal = trade.get("original_signal") or {}
    initial_sl = (
        trade.get("initial_stop_loss")
        or original_signal.get("stop_loss")
        or trade.get("stop_loss", 0)
    )
    # Risk is measured from the ACTUAL FILL to the INITIAL stop — that is the
    # capital genuinely put at risk when the position opened. (Not the signal's
    # theoretical entry, which was never transacted.)
    risk = abs(entry - initial_sl) if initial_sl else 0
    realized_rr = pnl_raw / risk if risk > 0 else 0

    entry_time = trade.get("entry_time")
    exit_time = trade.get("exit_time")
    duration = 0
    if entry_time and exit_time:
        try:
            duration = (exit_time - entry_time).total_seconds() / 60
        except AttributeError:
            duration = (exit_time - entry_time) / 60

    return {
        "pnl_pips": calculate_pips(symbol, entry, exit_p),
        "realized_rr": realized_rr,
        "duration_minutes": duration,
        "pnl_direction": pnl_raw,
    }


def calculate_sharpe(returns: list[float], periods_per_year: float = 252) -> float:
    """Annualized Sharpe ratio."""
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    mean = np.mean(arr)
    std = np.std(arr, ddof=1)
    if std == 0:
        return 0.0
    val = float((mean / std) * np.sqrt(periods_per_year))
    if np.isinf(val) or np.isnan(val):
        return 0.0
    return val


def calculate_sortino(returns: list[float], periods_per_year: float = 252) -> float:
    """Annualized Sortino ratio (only downside volatility)."""
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    mean = np.mean(arr)
    downside = arr[arr < 0]
    if len(downside) < 2:
        return 999.0
    downside_std = np.std(downside, ddof=1)
    if downside_std == 0:
        return 999.0
    val = float((mean / downside_std) * np.sqrt(periods_per_year))
    if np.isinf(val) or np.isnan(val):
        return 999.0
    return val


def calculate_max_drawdown(equity_curve: list[float], initial_balance: float | None = None) -> tuple[float, float]:
    """
    Calculate max drawdown from equity curve.
    Returns (max_dd_pct, max_dd_abs).

    max_dd_pct is expressed against `initial_balance` (fixed capital), not
    the rolling equity peak. Prop-firm drawdown limits (and this codebase's
    own daily/max drawdown circuit breaker) are evaluated as a fixed dollar
    loss relative to the account's starting balance — e.g. 5% of a $25,000
    account breaches the instant you're down $1,250, regardless of whether
    the account has since grown to $56,000 or $100,000. Dividing by the
    rolling peak instead means the same dollar loss reports a smaller and
    smaller % drawdown the more profitable the run becomes, which doesn't
    match what's actually being measured. If initial_balance isn't supplied,
    falls back to the first equity point (old behavior) for compatibility.
    """
    if not equity_curve:
        return 0.0, 0.0
    capital_base = initial_balance if initial_balance is not None and initial_balance > 0 else equity_curve[0]
    peak = equity_curve[0]
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for val in equity_curve:
        peak = max(peak, val)
        dd_abs = peak - val
        dd_pct = dd_abs / capital_base if capital_base > 0 else 0.0

        max_dd_abs = max(max_dd_abs, dd_abs)
        max_dd_pct = max(max_dd_pct, dd_pct)

    return max_dd_pct, max_dd_abs


def calculate_max_drawdown_of_peak(equity_curve: list[float]) -> float:
    """
    Max drawdown as a fraction of the rolling equity PEAK.

    This is the companion to `calculate_max_drawdown`, NOT a replacement for it.
    The capital-basis figure above is the right one for a prop-firm limit, but
    it is not comparable between runs that finish at different equity levels:
    the same proportional loss prints a bigger number the more the run made.
    A run that grows $10k -> $44k and gives back $11.2k reports 112% on the
    capital basis (which reads like a blown account) and 25% against its peak
    (which is what actually happened). Report both; compare runs on this one.
    """
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd_pct = 0.0
    for val in equity_curve:
        peak = max(peak, val)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, (peak - val) / peak)
    return max_dd_pct


def max_consecutive(values: list[bool]) -> int:
    """Return max consecutive True values."""
    max_count = 0
    current = 0
    for v in values:
        if v:
            current += 1
            max_count = max(max_count, current)
        else:
            current = 0
    return max_count


def _resolve_balance_before(trade: dict[str, Any], fallback: float) -> float:
    """
    dict.get(key, fallback) only applies `fallback` when the key is absent —
    if the key is present but explicitly None (as happens for grouped trades
    that never had a balance recorded), .get() returns None and any `> 0`
    check on the result raises TypeError. Treat None the same as missing.
    """
    val = trade.get("balance_before")
    return val if val is not None else fallback


def compute_portfolio_stats(
    trades: list[dict[str, Any]],
    initial_balance: float = 10000.0,
    sizing_basis: str = "STATIC",
) -> dict[str, Any]:
    """
    Compute aggregate portfolio statistics from a list of closed trades.
    Source: RiskManagement_Spec.md Section 8.2
    """
    if not trades:
        return {"total_trades": 0}

    if initial_balance is None:
        initial_balance = 10000.0

    pnls = [t.get("pnl", 0) for t in trades]
    wins = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) <= 0]

    win_pnls = [t["pnl"] for t in wins]
    loss_pnls = [abs(t["pnl"]) for t in losses]

    win_rate = len(wins) / len(trades)
    avg_win = np.mean(win_pnls) if win_pnls else 0
    avg_loss = np.mean(loss_pnls) if loss_pnls else 0

    gross_profit = sum(win_pnls) if win_pnls else 0
    gross_loss = sum(loss_pnls)

    # Build equity curve from balance_after when available (more accurate),
    # falling back to cumulative P&L if balance_after is missing.
    sorted_trades = sorted(trades, key=lambda t: t.get("exit_time") or t.get("entry_time") or 0)
    equity = [initial_balance]
    for t in sorted_trades:
        bal_after = t.get("balance_after")
        if bal_after is not None and bal_after > 0:
            equity.append(float(bal_after))
        else:
            equity.append(equity[-1] + t.get("pnl", 0))

    max_dd_pct, max_dd_abs = calculate_max_drawdown(equity, initial_balance)
    # Live parity: /api/stats reads this function, so without the peak-relative
    # companion the LIVE dashboard keeps showing only the capital basis — the
    # one that reads >100% on any account that has grown a lot. Backtests get
    # both via generate_risk_report; live should too.
    max_dd_pct_of_peak = calculate_max_drawdown_of_peak(equity)

    # Returns for Sharpe/Sortino. WHICH denominator is correct depends on
    # sizing_basis, because it decides what a trade's dollar P&L is a return ON.
    # Computed here (not earlier) so it can reuse the exit-ordered `equity`
    # series: equity[i] is the balance the i-th trade actually opened against.
    #
    # STATIC: dollar risk per trade is constant, so fixed capital is right.
    # Dividing by a growing balance would understate later returns' apparent
    # volatility purely because the account compounded up, silently inflating
    # Sharpe/Sortino over a winning run.
    #
    # BALANCE/EQUITY: risk genuinely scales with the account, so a $500 loss on
    # $50k IS the same risk event as $100 on $10k. Holding the denominator at
    # initial_balance makes later trades look progressively more volatile and
    # DEFLATES Sharpe — the same distortion mirrored.
    #
    # Mirrors frontend summaryEngine.js::computePeriodStats so the two agree.
    if sizing_basis in ("BALANCE", "EQUITY"):
        pct_returns = [
            (t.get("pnl", 0) / equity[i] if equity[i] > 0 else 0)
            for i, t in enumerate(sorted_trades)
        ]
    else:
        pct_returns = [
            t.get("pnl", 0) / initial_balance if initial_balance > 0 else 0
            for t in sorted_trades
        ]

    # TP breakdown
    exit_reasons = [t.get("exit_reason", "") for t in trades]
    total = len(trades)

    # Streaks
    is_win = [t.get("pnl", 0) > 0 for t in trades]
    is_loss = [t.get("pnl", 0) <= 0 for t in trades]

    return {
        "total_trades": total,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": win_rate,
        "total_pnl": sum(pnls),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 999.0,
        "expectancy": (win_rate * avg_win) - ((1 - win_rate) * avg_loss),
        "sharpe_ratio": calculate_sharpe(pct_returns),
        "sortino_ratio": calculate_sortino(pct_returns),
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_abs": max_dd_abs,
        "max_drawdown_pct_of_peak": max_dd_pct_of_peak,
        "max_consecutive_wins": max_consecutive(is_win),
        "max_consecutive_losses": max_consecutive(is_loss),
        "best_trade": max(pnls) if pnls else 0,
        "worst_trade": min(pnls) if pnls else 0,
        "tp1_hit_rate": exit_reasons.count("TP1") / total if total > 0 else 0,
        "tp2_hit_rate": exit_reasons.count("TP2") / total if total > 0 else 0,
        "tp3_hit_rate": exit_reasons.count("TP3") / total if total > 0 else 0,
        "tp4_hit_rate": exit_reasons.count("TP4") / total if total > 0 else 0,
        "tp5_hit_rate": exit_reasons.count("TP5") / total if total > 0 else 0,
        "sl_hit_rate": exit_reasons.count("SL") / total if total > 0 else 0,
        "trail_hit_rate": exit_reasons.count("TRAIL") / total if total > 0 else 0,
        "be_hit_rate": sum(1 for t in trades if t.get("be_applied")) / total if total > 0 else 0,
        "equity_curve": equity,
    }