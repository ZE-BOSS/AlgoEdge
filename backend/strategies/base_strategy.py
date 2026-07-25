"""
backend/strategies/base_strategy.py

Abstract base class for all trading strategies.
Source: TradingBot_MasterPlan-2.md Section 12 (Extension 5)
"""

from typing import Any

import pandas as pd
from pydantic import BaseModel


class TradeSignal(BaseModel):
    """Standardized output from any strategy."""
    strategy_id: str = "SMC_v1"
    symbol: str
    direction: str  # "BUY" or "SELL"
    signal_type: str = "OB_ENTRY"  # OB_ENTRY, FVG_ENTRY, BOS, CHOCH
    timeframe: str
    entry_price: float
    entry_zone_top: float = 0.0
    entry_zone_bottom: float = 0.0
    stop_loss: float
    take_profit: float
    confluence_score: int
    metadata: dict[str, Any] = {}
    chart_data: list[dict[str, Any]] | None = None

class TradeAction(BaseModel):
    """Action to take on an open position."""
    ticket: int
    action: str  # "CLOSE", "MODIFY_SL"
    new_sl: float | None = None
    close_pct: float = 1.0


class BaseStrategy:
    """Interface that all strategies must implement."""

    def __init__(self, user_config: Any):
        self.config = user_config
        self.is_backtesting = False
        self.run_logs = []

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        """Called on every new closed bar."""
        raise NotImplementedError

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> list[TradeAction] | None:
        """Called on every live tick for position management (optional)."""
        return []

    def get_required_timeframes(self) -> list[str]:
        """Return list of timeframes this strategy needs."""
        raise NotImplementedError
