"""
backend/backtester/report.py

Generates summary reports from backtest results.
"""

from typing import Any

from backend.analytics.metrics import compute_portfolio_stats


def generate_backtest_report(backtest_results: dict[str, Any]) -> dict[str, Any]:
    """
    Format backtest results into a standardized report.

    NOTE: currently unused/dead code — no callers anywhere in the codebase
    (confirmed via grep). `equity_curve`/`drawdown_curve`/`monthly_returns`
    are hardcoded empty here; the actual wired-in report path is
    `backend.analytics.reports.generate_risk_report`, called directly by
    `backtester/engine.py` and `backtester/portfolio_engine.py`. Do not
    assume this function is live without verifying a caller has been added.
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
