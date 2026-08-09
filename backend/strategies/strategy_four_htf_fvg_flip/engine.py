import pandas as pd
import pytz

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
                "tap_time": None,
                "m5_fvg": None,
                "m5_swing_point": None,
            }
            self.htf_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.2)
            self.m5_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.1)

    def _is_within_session(self, current_time: pd.Timestamp) -> bool:
        if not self.params.session_filter_enabled:
            return True
        
        ny_tz = pytz.timezone('America/New_York')
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize('UTC')
        ny_time = current_time.astimezone(ny_tz)
        time_str = ny_time.strftime("%H:%M")
        start = self.params.session_start
        cutoff = self.params.session_cutoff
        
        if start <= cutoff:
            return start <= time_str <= cutoff
        else:
            return time_str >= start or time_str <= cutoff

    def get_required_timeframes(self) -> list[str]:
        # Dynamically request timeframes configured by the user
        return [self.params.htf_timeframe, "M15", self.params.entry_confirmation_tf]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self._init_state(symbol)
        state = self.state[symbol]
        
        current_time = candles.index[-1]
        latest = candles.iloc[-1]
        
        # Process HTF (Keep trackers updated)
        if timeframe == self.params.htf_timeframe:
            self.htf_detectors[symbol].update(candles)

        # Process LTF Confirmation
        elif timeframe == self.params.entry_confirmation_tf:
            ltf_fvgs = []
            
            # 1. Update LTF FVGs
            if state["status"] in ["AWAIT_INVERSION_FVG", "AWAIT_INVERSION_CLOSE"]:
                ltf_fvgs = self.m5_detectors[symbol].update(candles)
            
            # 2. Check for HTF Tap (Real-time detection using M5 candle)
            if state["status"] == "AWAIT_HTF_TAP":
                htf_fvgs = self.htf_detectors[symbol].active_fvgs
                for fvg in htf_fvgs:
                    # Bullish FVG tap -> expect bounce up (BUY bias)
                    if fvg["type"] == "BULLISH" and fvg["bottom"] <= latest["low"] <= fvg["top"]:
                        state["status"] = "AWAIT_INVERSION_FVG"
                        state["bias"] = "BUY"
                        state["tap_time"] = current_time
                        self.log_event(f"[{symbol}] HTF Bullish FVG tapped by M5. Bias: BUY", category="FVG_FLIP")
                        break
                    # Bearish FVG tap -> expect bounce down (SELL bias)
                    elif fvg["type"] == "BEARISH" and fvg["bottom"] <= latest["high"] <= fvg["top"]:
                        state["status"] = "AWAIT_INVERSION_FVG"
                        state["bias"] = "SELL"
                        state["tap_time"] = current_time
                        self.log_event(f"[{symbol}] HTF Bearish FVG tapped by M5. Bias: SELL", category="FVG_FLIP")
                        break

            # 3. Look for a new LTF FVG in the OPPOSING direction (to be inverted)
            if state["status"] == "AWAIT_INVERSION_FVG":
                for fvg in reversed(ltf_fvgs):
                    fvg_time = fvg.get("index", pd.Timestamp.min)
                    if fvg_time >= state.get("tap_time", pd.Timestamp.min):
                        if state["bias"] == "BUY" and fvg["type"] == "BEARISH":
                            state["m5_fvg"] = fvg
                            state["status"] = "AWAIT_INVERSION_CLOSE"
                            lookback = candles.iloc[-20:]
                            state["m5_swing_point"] = lookback["low"].min()
                            self.log_event(f"[{symbol}] {timeframe} Bearish FVG formed. Awaiting Bullish inversion.", category="FVG_FLIP")
                            break
                        elif state["bias"] == "SELL" and fvg["type"] == "BULLISH":
                            state["m5_fvg"] = fvg
                            state["status"] = "AWAIT_INVERSION_CLOSE"
                            lookback = candles.iloc[-20:]
                            state["m5_swing_point"] = lookback["high"].max()
                            self.log_event(f"[{symbol}] {timeframe} Bullish FVG formed. Awaiting Bearish inversion.", category="FVG_FLIP")
                            break

            if state["status"] == "AWAIT_INVERSION_CLOSE":
                # Only block new trades outside session; do not reset state mid-setup
                if not self._is_within_session(current_time):
                    return None
                    
                fvg = state["m5_fvg"]
                triggered = False
                
                # Check for body close THROUGH the opposing FVG
                if state["bias"] == "BUY" and latest["close"] > fvg["top"]:
                    triggered = True
                elif state["bias"] == "SELL" and latest["close"] < fvg["bottom"]:
                    triggered = True

                # Invalidate if price breaks the swing extreme before inversion
                if state["bias"] == "BUY" and latest["close"] < state.get("m5_swing_point", 0):
                    state["status"] = "AWAIT_HTF_TAP"
                    self.log_event(f"[{symbol}] Inversion setup failed (Swing low broken).", category="FVG_FLIP")
                    return None
                elif state["bias"] == "SELL" and latest["close"] > state.get("m5_swing_point", float('inf')):
                    state["status"] = "AWAIT_HTF_TAP"
                    self.log_event(f"[{symbol}] Inversion setup failed (Swing high broken).", category="FVG_FLIP")
                    return None

                if triggered:
                    entry = latest["close"]
                    sl = state.get("m5_swing_point", entry * 0.99 if state["bias"]=="BUY" else entry * 1.01)
                    
                    rr = self.params.target_rr
                    if state["bias"] == "BUY":
                        tp = entry + (entry - sl) * rr
                    else:
                        tp = entry - (sl - entry) * rr
                    
                    # Reset state for next setup
                    state["status"] = "AWAIT_HTF_TAP"
                    
                    return TradeSignal(
                        symbol=symbol,
                        direction=state["bias"],
                        timeframe=timeframe,
                        entry_price=entry,
                        stop_loss=sl,
                        take_profit=tp,
                        confluence_score=88,
                        timestamp=float(latest.get("time", current_time.timestamp())),
                        metadata={"setup": "HTF_FVG_FLIP"}
                    )

        return None
