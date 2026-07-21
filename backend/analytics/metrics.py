"""
backend/analytics/metrics.py

Trade and portfolio metrics computation.
Source: TradingBot_MasterPlan-2.md Section 8 — Trade Metrics Computation
Source: RiskManagement_Spec.md Section 8
"""

import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def calculate_pips(symbol: str, price1: float, price2: float) -> float:
    """Calculate pip distance between two prices."""
    from backend.risk.position_sizer import get_pip_size
    pip_size = get_pip_size(symbol)
    if pip_size == 0:
        return 0.0
    return abs(price1 - price2) / pip_size


def compute_trade_metrics(trade: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute per-trade metrics after a trade closes.
    Source: TradingBot_MasterPlan-2.md — compute_trade_metrics
    """
    entry = trade.get("entry_price", 0)
    exit_p = trade.get("exit_price", 0)
    sl = trade.get("stop_loss", 0)
    direction = trade.get("direction", "BUY")
    symbol = trade.get("symbol", "")

    if direction == "BUY":
        pnl_raw = exit_p - entry
    else:
        pnl_raw = entry - exit_p

    risk = abs(entry - sl) if sl else 1
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


def calculate_sharpe(returns: List[float], periods_per_year: float = 252) -> float:
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


def calculate_sortino(returns: List[float], periods_per_year: float = 252) -> float:
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


def calculate_max_drawdown(equity_curve: List[float]) -> tuple[float, float]:
    """
    Calculate max drawdown from equity curve.
    Returns (max_dd_pct, max_dd_abs).
    """
    if not equity_curve:
        return 0.0, 0.0
    peak = equity_curve[0]
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd_abs = peak - val
        dd_pct = dd_abs / peak if peak > 0 else 0.0
        
        if dd_abs > max_dd_abs:
            max_dd_abs = dd_abs
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            
    return max_dd_pct, max_dd_abs


def max_consecutive(values: List[bool]) -> int:
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


def compute_portfolio_stats(trades: List[Dict[str, Any]], initial_balance: float = 10000.0) -> Dict[str, Any]:
    """
    Compute aggregate portfolio statistics from a list of closed trades.
    Source: RiskManagement_Spec.md Section 8.2
    """
    if not trades:
        return {"total_trades": 0}

    pnls = [t.get("pnl", 0) for t in trades]
    pct_returns = [t.get("pnl", 0) / t.get("balance_before", initial_balance) if t.get("balance_before", initial_balance) > 0 else 0 for t in trades]
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

    max_dd_pct, max_dd_abs = calculate_max_drawdown(equity)

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
    }
