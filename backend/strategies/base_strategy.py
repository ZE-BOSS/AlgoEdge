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

# [L1-opt] Cap on per-run in-memory strategy logs. A 50,000-bar run that logs
# once per bar would otherwise hold 50k dicts purely to be discarded, and the
# same list is serialised into the saved run.
_MAX_BACKTEST_RUN_LOGS = 5000

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

        # ── [T1.3] Confluence telemetry ──────────────────────────────────
        # Disabled by default, so live trading pays nothing for it: with
        # enabled=False, `gate()` is just `return bool(passed)`.
        # The ablation harness constructs the strategy and then sets
        # `strategy.gates.enabled = True` (optionally with disabled_gates).
        from backend.strategies.gate_recorder import GateRecorder
        self.gates = GateRecorder(enabled=False)

    # ── gate helpers ─────────────────────────────────────────────────────
    def gate(self, name: str, passed: Any, detail: str | None = None) -> bool:
        """
        Record a confluence outcome and return it.

        Call sites change minimally — `if not cond: return None` becomes
        `if not self.gate("name", cond): return None` — so instrumenting an
        engine does not move its control flow.
        """
        return self.gates.gate(name, passed, detail)

    def begin_candidate(self, symbol: str, timeframe: str, bar_index: int = -1, bar_time: Any = None) -> None:
        """Open a new candidate record at the top of on_bar()."""
        self.gates.begin(symbol, timeframe, bar_index, bar_time)

    def _tag_signal(self, signal: "TradeSignal | None") -> "TradeSignal | None":
        """
        Attach gate telemetry to an outgoing signal.

        Strategies return `self._tag_signal(sig)` instead of `sig` so the
        engine receives `metadata.passed_gates`, `metadata.gate_vector` and
        `metadata.confluence_tags`. Returns the signal unchanged when
        telemetry is off, so this is safe to leave in the live path.
        """
        if signal is None:
            return None
        if self.gates.enabled:
            self.gates.emitted(signal)
            try:
                signal.metadata = {**(signal.metadata or {}), **self.gates.metadata_for_signal()}
            except Exception:
                pass
        return signal

    def log_event(self, message: str, level: str = "INFO", category: str = "STRATEGY"):
        from datetime import datetime, timezone
        from backend.services.bot_service import bot_service
        
        # Terminal logging.
        # [L1-opt] During a backtest, strategy INFO chatter goes to DEBUG so the
        # loguru sink can filter it out before formatting/writing. Warnings and
        # errors still surface at full volume.
        if level == "DEBUG" or (self.is_backtesting and level == "INFO"):
            logger.debug(f"[{category}] {message}")
        elif level == "WARN":
            logger.warning(f"[{category}] {message}")
        elif level == "ERROR":
            logger.error(f"[{category}] {message}")
        else:
            logger.info(f"[{category}] {message}")
            
        if self.is_backtesting:
            # [L1-opt] Backtest logging was a measured bottleneck, not a nuisance.
            # Profiling DriftJumpAlpha over 2,200 bars: loguru `_log` accounted
            # for 11.4 s of 64 s (18%), with `TextIOWrapper.write` and
            # `_pickle.Pickler.dump` beneath it — the pickling is bot_service
            # marshalling each record for the WebSocket queue.
            #
            # A backtest emits tens of thousands of INFO lines that nobody reads
            # live; the run log is what the user actually reviews afterwards. So
            # keep the in-memory run_logs (bounded below) and forward only
            # WARN/ERROR to bot_service, instead of every INFO line.
            if len(self.run_logs) < _MAX_BACKTEST_RUN_LOGS:
                self.run_logs.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "level": level,
                    "category": category,
                    "message": message
                })
            elif len(self.run_logs) == _MAX_BACKTEST_RUN_LOGS:
                self.run_logs.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "level": "WARN",
                    "category": category,
                    "message": (
                        f"[run log truncated at {_MAX_BACKTEST_RUN_LOGS} entries — "
                        f"further messages suppressed for this run]"
                    ),
                })
            if level in ("WARN", "ERROR"):
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
