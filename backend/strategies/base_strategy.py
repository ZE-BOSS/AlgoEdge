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
    strategy_id: str = "APA_v1"
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
    timestamp: float | None = None
    metadata: dict[str, Any] = {}
    chart_data: list[dict[str, Any]] | None = None

class TradeAction(BaseModel):
    """Action to take on an open position."""
    ticket: int
    action: str          # "CLOSE", "MODIFY_SL"
    new_sl: float | None = None
    close_pct: float = 1.0
    close_reason: str = "STRATEGY_EXIT"  # Shown in exit_reason column


from backend.utils.logger import get_logger

logger = get_logger(__name__)

class BaseStrategy:
    """
    Interface that all strategies must implement.

    Fundamental gates (Phase 14 Stream 4)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Strategies can opt-in to fundamental filtering by instantiating a
    FundamentalGateRunner in __init__ and assigning it to self.fundamental_gates:

        from backend.strategies.core.fundamental_gate import (
            EconCalendarGate, FundamentalGateRunner,
        )
        self.fundamental_gates = FundamentalGateRunner([
            EconCalendarGate(buffer_minutes=15, impact_levels=["High"]),
        ], fail_loudly=True)

    The gate check is then made in on_bar() before returning a signal:

        if self.fundamental_gates:
            blocked, reason = self.fundamental_gates.check(signal, is_backtesting=self.is_backtesting)
            if blocked:
                self.log_event(f"Signal blocked by FundamentalGate: {reason}", level="INFO", category="GATE")
                return None

    Gates that require a live fetch are automatically skipped during backtests
    (is_backtesting=True), so no extra guard is needed in the strategy itself.
    """

    def __init__(self, user_config: Any):
        self.config = user_config
        self.is_backtesting = False
        self.run_logs = []
        # [Phase 14 Stream 4] Opt-in fundamental gate runner.
        # Strategies that want fundamental filtering assign a FundamentalGateRunner
        # here; the default None means no filtering applies.
        self.fundamental_gates = None

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

    def on_position_bar(
        self,
        symbol: str,
        timeframe: str,
        candles: "pd.DataFrame",
        position: dict,
    ) -> "TradeAction | None":
        """
        Called once per closed bar for every open position this strategy owns.

        Intentionally SYNCHRONOUS — `BacktestEngine.run()` is executed in a
        thread pool via `asyncio.to_thread`, so an `async` hook would require
        event-loop re-entry and defeat the purpose. Live execution similarly
        calls this from inside its bar-processing thread.

        Return a `TradeAction` to close or modify the position, or `None` to
        leave the risk engine in charge (the default).

        Phase 14 B2.3 primary use-case: APA's hard invalidation exit — if the
        current bar's body closes back beyond the Head level that defined the
        pattern, the thesis is invalidated and the trade is cut immediately.
        Without this hook the position would run to SL, TP, or trail despite the
        spec explicitly requiring an early exit under this condition.
        """
        return None

    def notify_outcome(self, symbol: str, group_id: str, is_win: bool, pnl: float) -> None:
        """
        Called by the backtester/live engine after a full signal group closes.
        Strategies that track internal per-day win/loss counters (e.g. VWAP's
        `losses_today`, BiasIFVG's `trades_today`) should override this method
        to update those counters from actual outcomes rather than guessing.
        Default implementation is a no-op.
        """
        pass
