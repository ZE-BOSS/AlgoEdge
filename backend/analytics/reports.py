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

    # Symbol breakdown
    per_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def generate_risk_report(trades: List[Dict[str, Any]]) -> RiskReport:
    """
    Generate a full RiskReport from a list of closed trades.
    """
    stats = compute_portfolio_stats(trades)

    if not trades:
        return RiskReport()

    # Calculate R-based metrics
    r_values = []
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        pnl = t.get("pnl", 0)
        risk = abs(entry - sl) if sl else 1
        r_val = pnl / risk if risk > 0 else 0
        r_values.append(r_val)

    win_r = [r for r in r_values if r > 0]
    loss_r = [r for r in r_values if r <= 0]

    # Session breakdown
    london_trades = [t for t in trades if t.get("session") == "LONDON"]
    ny_trades = [t for t in trades if t.get("session") == "NY"]
    overlap_trades = [t for t in trades if t.get("session") == "OVERLAP"]

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
        expectancy_r=stats["expectancy"],
        sharpe_ratio=stats["sharpe_ratio"],
        sortino_ratio=stats["sortino_ratio"],
        max_drawdown_pct=stats["max_drawdown_pct"],
        max_drawdown_abs=stats["max_drawdown_abs"],
        max_consecutive_wins=stats["max_consecutive_wins"],
        max_consecutive_losses=stats["max_consecutive_losses"],
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
        per_symbol=per_symbol,
    )
