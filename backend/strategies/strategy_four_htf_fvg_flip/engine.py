
import pandas as pd

from backend.core.config_schema import UserConfigV2
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.fvg import FVGDetector
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

@register_strategy("HTFFVGFlip_v1")
class HTFFVGFlipEngine(BaseStrategy):
    """
    Strategy 1: HTF Key Level -> 5M FVG -> Inversion Flip
    """
    def __init__(self, config: UserConfigV2):
        super().__init__(config)
        self.params = config.htf_fvg_flip
        
        # State tracking per symbol
        self.state = {}
        self.htf_detectors = {}
        self.m5_detectors = {}
        
    def _init_state(self, symbol: str):
        if symbol not in self.state:
            self.state[symbol] = {
                "status": "AWAIT_HTF_TAP",
                "bias": None,
                "htf_fvg": None,
                "m5_fvg": None,
                "m5_swing_point": None,
            }
            self.htf_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.2)
            self.m5_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.1)

    def _is_within_session(self, current_time: pd.Timestamp) -> bool:
        if not self.params.session_filter_enabled:
            return True
        
        if current_time.tz is not None:
            current_time = current_time.tz_localize(None)
            
        time_str = current_time.strftime("%H:%M")
        start = self.params.session_start
        cutoff = self.params.session_cutoff
        
        if start <= cutoff:
            return start <= time_str <= cutoff
        else:
            return time_str >= start or time_str <= cutoff

    def get_required_timeframes(self) -> list[str]:
        return ["H4", "M15", "M5"]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self._init_state(symbol)
        state = self.state[symbol]
        
        current_time = candles.index[-1]
        latest = candles.iloc[-1]
        
        # Process HTF (1H/4H)
        if timeframe == self.params.htf_timeframe:
            htf_fvgs = self.htf_detectors[symbol].update(candles)
            if state["status"] == "AWAIT_HTF_TAP":
                for fvg in htf_fvgs:
                    # Bullish FVG tap -> expect bounce up (BUY bias)
                    if fvg["type"] == "BULLISH" and fvg["bottom"] <= latest["low"] <= fvg["top"]:
                        state["status"] = "AWAIT_5M_FVG"
                        state["bias"] = "BUY"
                        self.log_event(f"[{symbol}] HTF Bullish FVG tapped. Bias: BUY", category="FVG_FLIP")
                        break
                    # Bearish FVG tap -> expect bounce down (SELL bias)
                    elif fvg["type"] == "BEARISH" and fvg["bottom"] <= latest["high"] <= fvg["top"]:
                        state["status"] = "AWAIT_5M_FVG"
                        state["bias"] = "SELL"
                        self.log_event(f"[{symbol}] HTF Bearish FVG tapped. Bias: SELL", category="FVG_FLIP")
                        break

        # Process M5
        elif timeframe == "M5":
            if state["status"] in ["AWAIT_5M_FVG", "AWAIT_5M_RETEST", "AWAIT_INVERSION"]:
                m5_fvgs = self.m5_detectors[symbol].update(candles)
                
            if state["status"] == "AWAIT_5M_FVG":
                # Look for a new M5 FVG in direction of bias
                for fvg in m5_fvgs:
                    if (state["bias"] == "BUY" and fvg["type"] == "BULLISH") or \
                       (state["bias"] == "SELL" and fvg["type"] == "BEARISH"):
                        state["m5_fvg"] = fvg
                        state["status"] = "AWAIT_5M_RETEST"
                        # Use last 20 candles for swing point detection
                        lookback = candles.iloc[-20:]
                        state["m5_swing_point"] = lookback["low"].min() if state["bias"] == "BUY" else lookback["high"].max()
                        self.log_event(f"[{symbol}] M5 {fvg['type']} FVG formed. Awaiting retest.", category="FVG_FLIP")
                        break

            elif state["status"] == "AWAIT_5M_RETEST":
                fvg = state["m5_fvg"]
                
                # Check if FVG invalidated (closed beyond)
                if state["bias"] == "BUY" and latest["close"] < fvg["bottom"]:
                    state["status"] = "AWAIT_HTF_TAP"
                    self.log_event(f"[{symbol}] M5 Bullish FVG invalidated.", category="FVG_FLIP")
                    return None
                elif state["bias"] == "SELL" and latest["close"] > fvg["top"]:
                    state["status"] = "AWAIT_HTF_TAP"
                    self.log_event(f"[{symbol}] M5 Bearish FVG invalidated.", category="FVG_FLIP")
                    return None
                    
                # Retest logic
                if state["bias"] == "BUY" and fvg["bottom"] <= latest["low"] <= fvg["top"]:
                    state["status"] = "AWAIT_INVERSION"
                    self.log_event(f"[{symbol}] M5 Bullish FVG retested. Awaiting M5 inversion.", category="FVG_FLIP")
                elif state["bias"] == "SELL" and fvg["bottom"] <= latest["high"] <= fvg["top"]:
                    state["status"] = "AWAIT_INVERSION"
                    self.log_event(f"[{symbol}] M5 Bearish FVG retested. Awaiting M5 inversion.", category="FVG_FLIP")

            # Process LTF Confirmation in the same timeframe if configured (M5)
            if state["status"] == "AWAIT_INVERSION":
                if not self._is_within_session(current_time):
                    # Timeout or outside session, reset
                    state["status"] = "AWAIT_HTF_TAP"
                    return None
                    
                fvg = state["m5_fvg"]
                
                # Check for body close through the M5 FVG
                triggered = False
                if state["bias"] == "BUY" and latest["close"] > fvg["top"] or state["bias"] == "SELL" and latest["close"] < fvg["bottom"]:
                    triggered = True

                if triggered:
                    entry = latest["close"]
                    sl = state.get("m5_swing_point", entry * 0.99 if state["bias"]=="BUY" else entry * 1.01)
                    
                    # Reset state
                    state["status"] = "AWAIT_HTF_TAP"
                    
                    return TradeSignal(
                        symbol=symbol,
                        direction=state["bias"],
                        entry_price=entry,
                        stop_loss=sl,
                        take_profit=0.0, # Managed dynamically by PositionManager
                        confluence_score=100.0,
                        metadata={"setup": "HTF_FVG_FLIP"}
                    )

        return None
