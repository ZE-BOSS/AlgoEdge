"""
backend/analytics/reports.py

RiskReport dataclass and generation.
Source: RiskManagement_Spec.md Section 8.2
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any
from backend.analytics.metrics import compute_portfolio_stats
from backend.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RiskReport:
    """
    Complete risk analytics report.
    Source: RiskManagement_Spec.md Section 8.2
    """
    # Core counts
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    # P&L
    total_pnl: float = 0.0
    total_pnl_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    best_trade_r: float = 0.0
    worst_trade_r: float = 0.0

    # Risk ratios
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Drawdown
    max_drawdown_pct: float = 0.0
    max_drawdown_abs: float = 0.0
    avg_drawdown_pct: float = 0.0
    max_drawdown_duration: int = 0

    # Streaks
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_consecutive_wins: float = 0.0
    avg_consecutive_losses: float = 0.0

    # TP performance breakdown
    tp1_hit_rate: float = 0.0
    tp2_hit_rate: float = 0.0
    tp3_hit_rate: float = 0.0
    tp4_hit_rate: float = 0.0
    tp5_hit_rate: float = 0.0
    sl_hit_rate: float = 0.0
    trail_hit_rate: float = 0.0
    be_hit_rate: float = 0.0

    # Session breakdown
    london_win_rate: float = 0.0
    ny_win_rate: float = 0.0
    overlap_win_rate: float = 0.0
    asian_win_rate: float = 0.0
    other_win_rate: float = 0.0

    # Symbol breakdown
    per_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Confluence stats
    confluence_stats: Dict[str, Any] = field(default_factory=dict)

    # Bias breakdown (BUY vs SELL)
    bias_stats: Dict[str, Any] = field(default_factory=dict)

    # Rejection Funnel
    rejection_funnel: Dict[str, Any] = field(default_factory=dict)


def generate_risk_report(trades: List[Dict[str, Any]]) -> RiskReport:
    """
    Generate a full RiskReport from a list of closed trades.
    """
    initial_balance = trades[0].get("balance_before", 10000.0) if trades else 10000.0
    stats = compute_portfolio_stats(trades, initial_balance=initial_balance)

    if not trades:
        return RiskReport()

    # Calculate R-based metrics
    r_values = []
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        exit_p = t.get("exit_price", 0)
        direction = t.get("direction", "BUY")
        risk_distance = abs(entry - sl) if sl else 0

        if risk_distance > 0 and entry > 0:
            # R = directional price move / risk distance (units cancel)
            if direction == "BUY":
                r_val = (exit_p - entry) / risk_distance
            else:
                r_val = (entry - exit_p) / risk_distance
        else:
            r_val = 0
        r_values.append(r_val)

    win_r = [r for r in r_values if r > 0]
    loss_r = [r for r in r_values if r <= 0]

    # Additional calculations for Drawdown, Calmar, and Streaks
    initial_balance = trades[0].get("balance_before", 10000.0) if trades else 10000.0
    sorted_trades = sorted(trades, key=lambda t: t.get("exit_time") or t.get("entry_time") or 0)
    equity = [initial_balance]
    for t in sorted_trades:
        bal_after = t.get("balance_after")
        if bal_after is not None and bal_after > 0:
            equity.append(float(bal_after))
        else:
            equity.append(equity[-1] + t.get("pnl", 0))

    drawdowns = []
    current_dd_duration = 0
    max_dd_duration = 0
    peak = equity[0]
    for val in equity:
        if val > peak:
            peak = val
            current_dd_duration = 0
        else:
            current_dd_duration += 1
            max_dd_duration = max(max_dd_duration, current_dd_duration)
            if peak > 0:
                drawdowns.append((peak - val) / peak)
    
    avg_drawdown_pct = sum(drawdowns) / len(drawdowns) if drawdowns else 0.0

    total_return_pct = (equity[-1] - equity[0]) / equity[0] if equity[0] > 0 else 0
    calmar_ratio = total_return_pct / stats["max_drawdown_pct"] if stats["max_drawdown_pct"] > 0 else float('inf')

    is_win = [t.get("pnl", 0) > 0 for t in sorted_trades]
    win_streaks = []
    loss_streaks = []
    current_win_streak = 0
    current_loss_streak = 0
    for w in is_win:
        if w:
            if current_loss_streak > 0:
                loss_streaks.append(current_loss_streak)
                current_loss_streak = 0
            current_win_streak += 1
        else:
            if current_win_streak > 0:
                win_streaks.append(current_win_streak)
                current_win_streak = 0
            current_loss_streak += 1
    if current_win_streak > 0:
        win_streaks.append(current_win_streak)
    if current_loss_streak > 0:
        loss_streaks.append(current_loss_streak)

    avg_consecutive_wins = sum(win_streaks) / len(win_streaks) if win_streaks else 0.0
    avg_consecutive_losses = sum(loss_streaks) / len(loss_streaks) if loss_streaks else 0.0

    # Session breakdown (detect_session returns "LONDON/NY" for overlap)
    london_trades = [t for t in trades if t.get("entry_session") == "LONDON"]
    ny_trades = [t for t in trades if t.get("entry_session") == "NY"]
    overlap_trades = [t for t in trades if t.get("entry_session") in ("OVERLAP", "LONDON/NY")]

    def session_wr(session_trades):
        if not session_trades:
            return 0.0
        wins = sum(1 for t in session_trades if t.get("pnl", 0) > 0)
        return wins / len(session_trades)

    # Per-symbol breakdown
    symbols = set(t.get("symbol", "") for t in trades)
    per_symbol = {}
    for sym in symbols:
        sym_trades = [t for t in trades if t.get("symbol") == sym]
        sym_stats = compute_portfolio_stats(sym_trades)
        per_symbol[sym] = {
            "win_rate": sym_stats.get("win_rate", 0),
            "pnl": sym_stats.get("total_pnl", 0),
            "trades": sym_stats.get("total_trades", 0),
        }

    # Confluence breakdown
    confluence_stats = {
        "by_score": {},
        "by_confirmation": {}
    }
    
    for t in trades:
        score = t.get("confluence_score", 0)
        is_win = t.get("pnl", 0) > 0
        
        # Track by score
        if score not in confluence_stats["by_score"]:
            confluence_stats["by_score"][score] = {"trades": 0, "wins": 0}
        confluence_stats["by_score"][score]["trades"] += 1
        if is_win:
            confluence_stats["by_score"][score]["wins"] += 1
            
        # Track by confirmation (need original signal metadata)
        original_signal = t.get("original_signal", {})
        metadata = original_signal.get("metadata", {})
        breakdown = metadata.get("score_breakdown", {})
        if not breakdown:
            # Try to reconstruct basic breakdown if not in original signal
            breakdown = {}
            if score >= 40: breakdown["base_structure"] = 40
            
        for conf_type, val in breakdown.items():
            if val > 0:
                if conf_type not in confluence_stats["by_confirmation"]:
                    confluence_stats["by_confirmation"][conf_type] = {"trades": 0, "wins": 0}
                confluence_stats["by_confirmation"][conf_type]["trades"] += 1
                if is_win:
                    confluence_stats["by_confirmation"][conf_type]["wins"] += 1
                    
    # Calculate win rates
    for s, data in confluence_stats["by_score"].items():
        data["win_rate"] = data["wins"] / data["trades"] if data["trades"] > 0 else 0.0
        
    for c, data in confluence_stats["by_confirmation"].items():
        data["win_rate"] = data["wins"] / data["trades"] if data["trades"] > 0 else 0.0

    # Bias breakdown (BUY vs SELL win rate)
    buy_trades  = [t for t in trades if t.get("direction") == "BUY"]
    sell_trades = [t for t in trades if t.get("direction") == "SELL"]

    def _bias_stats(grp):
        if not grp:
            return {"trades": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0}
        wins = sum(1 for t in grp if t.get("pnl", 0) > 0)
        return {
            "trades": len(grp),
            "wins": wins,
            "win_rate": wins / len(grp),
            "total_pnl": sum(t.get("pnl", 0) for t in grp),
        }

    bias_stats = {
        "BUY":  _bias_stats(buy_trades),
        "SELL": _bias_stats(sell_trades),
    }

    def safe_float(val, default=0.0):
        try:
            f = float(val)
            if np.isinf(f) or np.isnan(f): return default
            return f
        except: return default

    return RiskReport(
        total_trades=stats["total_trades"],
        winning_trades=stats["winning_trades"],
        losing_trades=stats["losing_trades"],
        win_rate=stats["win_rate"],
        total_pnl=stats["total_pnl"],
        total_pnl_r=sum(r_values),
        avg_win_r=float(sum(win_r) / len(win_r)) if win_r else 0,
        avg_loss_r=float(sum(loss_r) / len(loss_r)) if loss_r else 0,
        best_trade_r=max(r_values) if r_values else 0,
        worst_trade_r=min(r_values) if r_values else 0,
        profit_factor=stats["profit_factor"],
        expectancy_r=safe_float((stats["win_rate"] * (sum(win_r) / len(win_r) if win_r else 0)) - ((1 - stats["win_rate"]) * (sum(loss_r) / len(loss_r) if loss_r else 0))),
        sharpe_ratio=safe_float(stats["sharpe_ratio"]),
        sortino_ratio=safe_float(stats["sortino_ratio"]),
        calmar_ratio=safe_float(calmar_ratio),
        max_drawdown_pct=stats["max_drawdown_pct"],
        max_drawdown_abs=stats["max_drawdown_abs"],
        avg_drawdown_pct=avg_drawdown_pct,
        max_drawdown_duration=max_dd_duration,
        max_consecutive_wins=stats["max_consecutive_wins"],
        max_consecutive_losses=stats["max_consecutive_losses"],
        avg_consecutive_wins=avg_consecutive_wins,
        avg_consecutive_losses=avg_consecutive_losses,
        tp1_hit_rate=stats["tp1_hit_rate"],
        tp2_hit_rate=stats["tp2_hit_rate"],
        tp3_hit_rate=stats["tp3_hit_rate"],
        tp4_hit_rate=stats["tp4_hit_rate"],
        tp5_hit_rate=stats["tp5_hit_rate"],
        sl_hit_rate=stats["sl_hit_rate"],
        trail_hit_rate=stats["trail_hit_rate"],
        be_hit_rate=stats["be_hit_rate"],
        london_win_rate=session_wr(london_trades),
        ny_win_rate=session_wr(ny_trades),
        overlap_win_rate=session_wr(overlap_trades),
        asian_win_rate=session_wr([t for t in trades if t.get("entry_session") == "ASIAN"]),
        other_win_rate=session_wr([t for t in trades if t.get("entry_session") not in ["LONDON", "NY", "LONDON/NY", "ASIAN"]]),
        per_symbol=per_symbol,
        confluence_stats=confluence_stats,
        bias_stats=bias_stats,
    )
