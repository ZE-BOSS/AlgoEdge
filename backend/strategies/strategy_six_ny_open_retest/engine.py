
import pandas as pd
from backend.core.config_schema import UserConfigV2
from backend.strategies.base_strategy import BaseStrategy, Signal
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

@register_strategy("NYOpenRetest_v1")
class NYOpenRetestEngine(BaseStrategy):
    """
    Strategy 3: 8:00 AM Session-Range Break & Retest
    """
    def __init__(self, config: UserConfigV2):
        super().__init__(config)
        self.params = config.ny_open_retest
        self.state = {}
        
    def _init_state(self, symbol: str):
        if symbol not in self.state:
            self.state[symbol] = {
                "status": "MARK_RANGE",
                "range_high": None,
                "range_low": None,
                "range_mid": None,
                "bias": None,
                "current_day": None
            }

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> Signal | None:
        self._init_state(symbol)
        state = self.state[symbol]
        
        current_time = candles.index[-1]
        latest = candles.iloc[-1]
        
        # Reset state at the start of a new day
        current_day = current_time.date()
        if state["current_day"] != current_day:
            state["current_day"] = current_day
            state["status"] = "MARK_RANGE"
            state["range_high"] = None
            state["range_low"] = None
            state["range_mid"] = None
            state["bias"] = None

        time_str = current_time.strftime("%H:%M")

        # 1. Mark the Range on M15
        if timeframe == "M15":
            if state["status"] == "MARK_RANGE" and time_str == self.params.range_window_end:
                # Assuming the M15 candle closing at 08:15 covers the 08:00-08:15 period
                state["range_high"] = latest["high"]
                state["range_low"] = latest["low"]
                state["range_mid"] = (latest["high"] + latest["low"]) / 2.0
                state["status"] = "AWAIT_BREAK"
                logger.info(f"[{symbol}] NY Open Range marked: {state['range_high']} - {state['range_low']} (Mid: {state['range_mid']})")

        # 2. Break and Retest on M1
        elif timeframe == "M1":
            # Session expiration check
            if time_str >= self.params.session_end and state["status"] not in ["MARK_RANGE", "DONE"]:
                state["status"] = "DONE"
                logger.info(f"[{symbol}] Session ended, stopping for the day.")
                return None
                
            if state["status"] == "AWAIT_BREAK":
                if time_str < self.params.earliest_valid_break_time:
                    return None
                    
                if latest["close"] > state["range_high"]:
                    state["bias"] = "LONG"
                    state["status"] = "AWAIT_RETEST"
                    logger.info(f"[{symbol}] NY Open bullish break detected. Awaiting retest to {state['range_mid']}")
                elif latest["close"] < state["range_low"]:
                    state["bias"] = "SHORT"
                    state["status"] = "AWAIT_RETEST"
                    logger.info(f"[{symbol}] NY Open bearish break detected. Awaiting retest to {state['range_mid']}")

            elif state["status"] == "AWAIT_RETEST":
                triggered = False
                if state["bias"] == "LONG" and latest["low"] <= state["range_mid"] or state["bias"] == "SHORT" and latest["high"] >= state["range_mid"]:
                    triggered = True
                    
                if triggered:
                    entry = state["range_mid"]
                    
                    # Convert fixed points to actual price deltas based on pip size if needed.
                    # Assuming price is raw for now, as standard for the engine API to handle point conversions internally.
                    # Or rely on position manager for fixed targets.
                    buffer = self.params.stop_buffer_points
                    target = self.params.fixed_target_points
                    
                    stop_loss = state["range_low"] - buffer if state["bias"] == "LONG" else state["range_high"] + buffer
                    take_profit = entry + target if state["bias"] == "LONG" else entry - target
                    
                    state["status"] = "DONE"
                    return Signal(
                        symbol=symbol,
                        direction=state["bias"],
                        entry_price=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        confluence_score=100.0,
                        metadata={"setup": "NY_OPEN_RETEST"}
                    )

        return None
