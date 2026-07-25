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


from backend.utils.logger import get_logger

logger = get_logger(__name__)

class BaseStrategy:
    """Interface that all strategies must implement."""

    def __init__(self, user_config: Any):
        self.config = user_config
        self.is_backtesting = False
        self.run_logs = []

    def log_event(self, message: str, level: str = "INFO", category: str = "STRATEGY"):
        from datetime import datetime, timezone
        from backend.services.bot_service import bot_service
        
        # Terminal logging
        if level == "DEBUG":
            logger.debug(f"[{category}] {message}")
        elif level == "WARN":
            logger.warning(f"[{category}] {message}")
        elif level == "ERROR":
            logger.error(f"[{category}] {message}")
        else:
            logger.info(f"[{category}] {message}")
            
        if self.is_backtesting:
            self.run_logs.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "category": category,
                "message": message
            })
            if level != "DEBUG":
                bot_service.log_system_event(message, level, f"BT-{category}")
        else:
            bot_service.log_system_event(message, level, category)

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        """Called on every new closed bar."""
        raise NotImplementedError

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> list[TradeAction] | None:
        """Called on every live tick for position management (optional)."""
        return []

    def get_required_timeframes(self) -> list[str]:
        """Return list of timeframes this strategy needs."""
        raise NotImplementedError
