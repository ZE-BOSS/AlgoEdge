
import pandas as pd

from backend.core.config_schema import UserConfigV2
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
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

    def get_required_timeframes(self) -> list[str]:
        return ["H4", "M15", "M5"]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self._init_state(symbol)
        state = self.state[symbol]
        
        current_time = candles.index[-1]
        latest = candles.iloc[-1]
        
        # 1. Determine Bias
        if timeframe in ["H1", "H4", "D1"]:
            # Dummy SMA-based bias for the boilerplate
            close_prices = candles["close"]
            sma20 = close_prices.rolling(20).mean().iloc[-1]
            if pd.isna(sma20):
                return None
                
            if candles.iloc[-1]["close"] > sma20:
                state["bias"] = "BUY"
            else:
                state["bias"] = "SELL"
                
            if state["status"] == "AWAIT_BIAS" and state["bias"] != "NEUTRAL":
                state["status"] = "AWAIT_KEY_LEVEL"
                self.log_event(f"[{symbol}] Bias established: {state['bias']}", category="BIAS_IFVG")
        
          # 3. M1 IFVG Entry Trigger
        elif timeframe == "M5":
            if state["status"] == "AWAIT_KEY_LEVEL":
                # Simulated key level tap for boilerplate
                if state["bias"] == "BUY" and latest["close"] < candles.iloc[-2]["low"]:
                    state["status"] = "AWAIT_IFVG_CLOSE"
                elif state["bias"] == "SELL" and latest["close"] > candles.iloc[-2]["high"]:
                    state["status"] = "AWAIT_IFVG_CLOSE"
                
            if state["status"] == "AWAIT_IFVG_CLOSE":
                if not self._is_within_session(current_time):
                    state["status"] = "AWAIT_KEY_LEVEL"
                    return None
                    
                # Simulated trigger logic for boilerplate
                triggered = False
                if state["bias"] == "BUY" and latest["close"] > candles.iloc[-2]["high"]:
                    triggered = True
                elif state["bias"] == "SELL" and latest["close"] < candles.iloc[-2]["low"]:
                    triggered = True
                    
                if triggered:
                    entry = latest["close"]
                    sl = entry * 0.99 if state["bias"] == "BUY" else entry * 1.01
                    
                    state["status"] = "AWAIT_KEY_LEVEL"
                    state["trades_today"] += 1
                    
                    return TradeSignal(
                        symbol=symbol,
                        direction=state["bias"],
                        entry_price=entry,
                        stop_loss=sl,
                        take_profit=0.0,
                        confluence_score=100.0,
                        metadata={"setup": "BIAS_IFVG"}
                    )
                    
            # Reset daily limits at end of day
            if current_time.hour == 23 and current_time.minute >= 50:
                state["trades_today"] = 0

        return None
