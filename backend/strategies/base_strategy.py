"""
backend/strategies/base_strategy.py

Abstract base class for all trading strategies.
Source: TradingBot_MasterPlan-2.md Section 12 (Extension 5)
"""

import pandas as pd
from typing import Optional, Dict, Any, List
from pydantic import BaseModel

class TradeSignal(BaseModel):
    """Standardized output from any strategy."""
    strategy_id: str
    symbol: str
    direction: str  # "BULLISH" or "BEARISH"
    timeframe: str
    entry_zone_top: float
    entry_zone_bottom: float
    stop_loss: float
    take_profit: float
    confluence_score: int
    metadata: Dict[str, Any] = {}

class TradeAction(BaseModel):
    """Action to take on an open position."""
    ticket: int
    action: str  # "CLOSE", "MODIFY_SL"
    new_sl: Optional[float] = None
    close_pct: float = 1.0


class BaseStrategy:
    """Interface that all strategies must implement."""

    def __init__(self, user_config: Any):
        self.config = user_config

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> Optional[TradeSignal]:
        """Called on every new closed bar."""
        raise NotImplementedError

    async def on_tick(self, symbol: str, tick: Dict[str, Any]) -> Optional[List[TradeAction]]:
        """Called on every live tick for position management (optional)."""
        return []

    def get_required_timeframes(self) -> List[str]:
        """Return list of timeframes this strategy needs."""
        raise NotImplementedError
