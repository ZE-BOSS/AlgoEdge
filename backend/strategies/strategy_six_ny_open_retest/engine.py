import pandas as pd
import pytz

from backend.core.config_schema import UserConfigV2
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
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

    def get_required_timeframes(self) -> list[str]:
        # Spec (strategy-3-nyopen-break-retest.md): only M15 (range marking) and M5 (break+retest)
        return ["M15", "M5"]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
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

        ny_tz = pytz.timezone('America/New_York')
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize('UTC')
        ny_time = current_time.astimezone(ny_tz)
        time_str = ny_time.strftime("%H:%M")

        # 1. Mark the Range on M15
        if timeframe == "M15":
            if state["status"] == "MARK_RANGE":
                # Only accumulate candles that fall inside [range_window_start, range_window_end]
                if self.params.range_window_start <= time_str <= self.params.range_window_end:
                    state["range_high"] = max(state["range_high"], latest["high"]) if state["range_high"] else latest["high"]
                    state["range_low"] = min(state["range_low"], latest["low"]) if state["range_low"] else latest["low"]
                elif time_str > self.params.range_window_end and state["range_high"] is not None:
                    state["range_mid"] = (state["range_high"] + state["range_low"]) / 2.0
                    state["status"] = "AWAIT_BREAK"
                    self.log_event(f"[{symbol}] NY Open Range marked: {state['range_high']} - {state['range_low']} (Mid: {state['range_mid']})", category="NY_OPEN")


        # 2. Break and Retest on M5
        elif timeframe == "M5":
            # Session expiration check
            if time_str >= self.params.session_end and state["status"] not in ["MARK_RANGE", "DONE"]:
                state["status"] = "DONE"
                self.log_event(f"[{symbol}] Session ended, stopping for the day.", category="NY_OPEN")
                return None
                
            if state["status"] == "AWAIT_BREAK":
                if time_str < self.params.earliest_valid_break_time:
                    return None
                    
                if latest["close"] > state["range_high"]:
                    state["bias"] = "BUY"
                    state["status"] = "AWAIT_RETEST"
                    self.log_event(f"[{symbol}] NY Open bullish break detected. Awaiting retest to {state['range_mid']}", category="NY_OPEN")
                elif latest["close"] < state["range_low"]:
                    state["bias"] = "SELL"
                    state["status"] = "AWAIT_RETEST"
                    self.log_event(f"[{symbol}] NY Open bearish break detected. Awaiting retest to {state['range_mid']}", category="NY_OPEN")

            elif state["status"] == "AWAIT_RETEST":
                triggered = False
                if (state["bias"] == "BUY" and latest["low"] <= state["range_mid"]) or (state["bias"] == "SELL" and latest["high"] >= state["range_mid"]):
                    triggered = True
                    
                if triggered:
                    entry = state["range_mid"]
                    
                    # Convert fixed points to actual price deltas based on pip size if needed.
                    # Assuming price is raw for now, as standard for the engine API to handle point conversions internally.
                    # Or rely on position manager for fixed targets.
                    buffer = self.params.stop_buffer_points
                    target = self.params.fixed_target_points
                    
                    stop_loss = state["range_low"] - buffer if state["bias"] == "BUY" else state["range_high"] + buffer
                    take_profit = entry + target if state["bias"] == "BUY" else entry - target
                    
                    if getattr(self.params, 'dynamic_target_override', True):
                        recent_candles = candles.iloc[-50:]
                        if state["bias"] == "BUY":
                            recent_high = recent_candles["high"].max()
                            swing_dist = recent_high - entry
                            # Spec: use NEARER swing level if it's closer than the fixed target
                            # — avoids holding through a resistance level just to reach the fixed TP.
                            if 0 < swing_dist < target:
                                take_profit = recent_high
                        else:
                            recent_low = recent_candles["low"].min()
                            swing_dist = entry - recent_low
                            if 0 < swing_dist < target:
                                take_profit = recent_low
                    
                    state["status"] = "DONE"
                    return TradeSignal(
                        strategy_id="NYOpenRetest_v1",
                        symbol=symbol,
                        direction=state["bias"],
                        timeframe=timeframe,
                        entry_price=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        confluence_score=92,
                        timestamp=float(latest["time"]) if "time" in latest else candles.index[-1].timestamp(),
                        metadata={"setup": "NY_OPEN_RETEST"}
                    )

        return None
