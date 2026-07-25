
import pandas as pd
from backend.core.config_schema import UserConfigV2
from backend.strategies.base_strategy import BaseStrategy, Signal
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

@register_strategy("BiasIFVG_v1")
class BiasIFVGEngine(BaseStrategy):
    """
    Strategy 2: 4-Step Bias -> Key Level -> IFVG
    """
    def __init__(self, config: UserConfigV2):
        super().__init__(config)
        self.params = config.bias_ifvg
        
        # State tracking per symbol
        self.state = {}
        
    def _init_state(self, symbol: str):
        if symbol not in self.state:
            self.state[symbol] = {
                "bias": None,
                "key_levels": [],
                "active_level": None,
                "manipulation_leg": None,
                "ifvg_detected": False,
                "status": "AWAIT_KEY_LEVEL",
                "trades_today": 0,
                "last_trade_won": False
            }

    def _is_within_session(self, current_time: pd.Timestamp) -> bool:
        if current_time.tz is not None:
            current_time = current_time.tz_localize(None)
            
        time_str = current_time.strftime("%H:%M")
        start = self.params.session_start
        cutoff = self.params.session_cutoff
        
        if start <= cutoff:
            return start <= time_str <= cutoff
        else:
            return time_str >= start or time_str <= cutoff

    def _compute_bias(self, candles: pd.DataFrame) -> str:
        # Placeholder for complex HTF bias computation (FVG respect/disrespect)
        # Using a simple moving average proxy for the boilerplate
        if len(candles) < 20:
            return "UNKNOWN"
        sma20 = candles["close"].rolling(20).mean().iloc[-1]
        if candles.iloc[-1]["close"] > sma20:
            return "LONG"
        return "SHORT"
        
    def _detect_key_levels(self, candles: pd.DataFrame, bias: str):
        # Placeholder for FVG, CISD, and Rejection Block detectors
        # In a full implementation, we'd scan multiple timeframes and merge overlaps
        return []

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> Signal | None:
        self._init_state(symbol)
        state = self.state[symbol]
        
        current_time = candles.index[-1]
        latest = candles.iloc[-1]
        
        # 1. Higher-Timeframe Bias Generation
        if timeframe in ["H1", "H4", "D1"]:
            new_bias = self._compute_bias(candles)
            if new_bias != state["bias"]:
                state["bias"] = new_bias
                state["key_levels"] = self._detect_key_levels(candles, state["bias"])
                state["status"] = "AWAIT_KEY_LEVEL"
                logger.info(f"[{symbol}] Bias updated to {new_bias} based on {timeframe}")
        
        # 2 & 3. Key Level Tap and Confirmation
        elif timeframe == "M5":
            if state["status"] == "AWAIT_KEY_LEVEL":
                # Simulated key level tap for boilerplate
                # E.g., if price drops sharply while bias is LONG
                pass
                
        elif timeframe == "M1":
            if state["status"] == "AWAIT_IFVG_CLOSE":
                if not self._is_within_session(current_time):
                    state["status"] = "AWAIT_KEY_LEVEL"
                    return None
                    
                # Simulated trigger logic for boilerplate
                triggered = False
                if triggered:
                    entry = latest["close"]
                    sl = entry * 0.99 if state["bias"] == "LONG" else entry * 1.01
                    
                    state["status"] = "AWAIT_KEY_LEVEL"
                    state["trades_today"] += 1
                    
                    return Signal(
                        symbol=symbol,
                        direction=state["bias"],
                        entry_price=entry,
                        stop_loss=sl,
                        take_profit=0.0,
                        confluence_score=100.0,
                        metadata={"setup": "BIAS_IFVG"}
                    )
                    
            # Reset daily limits at end of day
            if current_time.hour == 23 and current_time.minute == 59:
                state["trades_today"] = 0

        return None
