"""
backend/backtester/optimizer.py

Grid search parameter optimization.
Uses BacktestEngine to run multiple backtests across a parameter grid.
Source: TradingBot_MasterPlan-2.md Section 11
"""

import itertools
from copy import deepcopy
from typing import Any

import pandas as pd

from backend.backtester.engine import BacktestEngine
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class ParameterOptimizer:
    """Runs multiple backtests across a grid of parameters."""

    def __init__(self, base_config: dict[str, Any]):
        self.base_config = base_config

    def run_grid_search(
        self,
        candles: pd.DataFrame,
        signals: list[dict[str, Any]],
        param_grid: dict[str, list[Any]],
        initial_balance: float = 10000.0,
        rank_by: str = "sharpe_ratio",
    ) -> list[dict[str, Any]]:
        """
        Execute backtests for every combination in param_grid.
        Returns list of results sorted by the specified metric (descending).
        """
        # Generate all combinations
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(itertools.product(*values))

        logger.info(f"Grid search: {len(combinations)} combinations across {keys}")
        results = []

        for combo in combinations:
            # Build config for this combination
            config = deepcopy(self.base_config)
            param_set = {}
            for i, key in enumerate(keys):
                config[key] = combo[i]
                param_set[key] = combo[i]

            # Run backtest
            try:
                engine = BacktestEngine(config)
                result = engine.run(candles, signals, initial_balance)
                report = result.get("report")

                results.append({
                    "params": param_set,
                    "total_trades": report.total_trades if report else 0,
                    "win_rate": report.win_rate if report else 0,
                    "total_pnl": report.total_pnl if report else 0,
                    "sharpe_ratio": report.sharpe_ratio if report else 0,
                    "profit_factor": report.profit_factor if report else 0,
                    "max_drawdown_pct": report.max_drawdown_pct if report else 0,
                    "final_balance": result.get("final_balance", initial_balance),
                })
            except Exception as e:
                logger.warning(f"Backtest failed for params {param_set}: {e}")
                continue

        # Sort by target metric (descending)
        results.sort(key=lambda x: x.get(rank_by, 0), reverse=True)
        logger.info(f"Grid search complete. Best {rank_by}: {results[0].get(rank_by, 0):.4f}" if results else "No results")
        return results
