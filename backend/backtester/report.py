"""
backend/backtester/report.py

Generates summary reports from backtest results.
"""

from typing import Dict, Any
from backend.analytics.metrics import compute_portfolio_stats

def generate_backtest_report(backtest_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format backtest results into a standardized report.
    """
    trades = backtest_results.get("trades", [])
    initial_balance = backtest_results.get("initial_balance", 10000.0)
    stats = compute_portfolio_stats(trades, initial_balance)
    
    report = {
        "summary": stats,
        "equity_curve": [],
        "drawdown_curve": [],
        "monthly_returns": {},
        "trade_log": trades
    }
    
    return report
