import pandas as pd
import pytz

from backend.core.config_schema import UserConfigV2
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.fvg import FVGDetector
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
        
        self.state = {}
        self.htf_detectors = {}
        self.m15_detectors = {}
        self.m5_detectors = {}
        self.last_trade_date = {}
        
    def _init_state(self, symbol: str):
        if symbol not in self.state:
            self.state[symbol] = {
                "bias": None,
                "key_level": None,
                "status": "AWAIT_BIAS",
                "m5_fvg_to_invert": None,
                "m5_swing_point": None,
                "manipulation_leg_start": None,
                "trades_today": 0,
            }
            self.htf_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.1)
            self.m15_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.1)
            self.m5_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.05)

    def _is_within_session(self, current_time: pd.Timestamp) -> bool:
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize('UTC')
        ny_tz = pytz.timezone('America/New_York')
        ny_time = current_time.astimezone(ny_tz)
        time_str = ny_time.strftime("%H:%M")
        
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
        
        current_date = current_time.date()
        if symbol not in self.last_trade_date or self.last_trade_date[symbol] != current_date:
            self.state[symbol] = {
                "bias": None,
                "key_level": None,
                "status": "AWAIT_BIAS",
                "m5_fvg_to_invert": None,
                "m5_swing_point": None,
                "manipulation_leg_start": None,
                "trades_today": 0,
            }
            state = self.state[symbol]
            self.last_trade_date[symbol] = current_date
            self.log_event(f"[{symbol}] State reset for new trading day.", category="BIAS_IFVG")
        
        # 1. Determine Bias (HTF: H4)
        if timeframe == "H4":
            htf_fvgs = self.htf_detectors[symbol].update(candles)
            if htf_fvgs:
                # Bias holds as long as we have a recent unresolved HTF FVG
                last_fvg = htf_fvgs[-1]
                state["bias"] = "BUY" if last_fvg["type"] == "BULLISH" else "SELL"
                if state["status"] == "AWAIT_BIAS":
                    state["status"] = "AWAIT_KEY_LEVEL"
                    self.log_event(f"[{symbol}] Bias established: {state['bias']} based on HTF FVG", category="BIAS_IFVG")
        
        # 2. Key Levels (M15 tracker update)
        elif timeframe == "M15":
            self.m15_detectors[symbol].update(candles)

        # 3. IFVG Confirmation and Entry (M5)
        elif timeframe == "M5":
            m5_fvgs = []
            
            # Update M5 FVGs
            if state["status"] in ["AWAIT_IFVG_SETUP", "AWAIT_IFVG_CLOSE"]:
                m5_fvgs = self.m5_detectors[symbol].update(candles)
                
            # Check for M15 Tap (Real-time detection using M5 candle)
            if state["status"] in ["AWAIT_KEY_LEVEL", "AWAIT_IFVG_SETUP"]:
                m15_fvgs = self.m15_detectors[symbol].active_fvgs
                for fvg in reversed(m15_fvgs):
                    if state["bias"] == "BUY" and fvg["type"] == "BULLISH" and fvg["bottom"] <= latest["low"] <= fvg["top"]:
                        state["key_level"] = fvg
                        state["status"] = "AWAIT_IFVG_SETUP"
                        state["manipulation_leg_start"] = current_time
                        self.log_event(f"[{symbol}] M15 Bullish FVG tapped by M5. Awaiting M5 IFVG.", category="BIAS_IFVG")
                        break
                    elif state["bias"] == "SELL" and fvg["type"] == "BEARISH" and fvg["bottom"] <= latest["high"] <= fvg["top"]:
                        state["key_level"] = fvg
                        state["status"] = "AWAIT_IFVG_SETUP"
                        state["manipulation_leg_start"] = current_time
                        self.log_event(f"[{symbol}] M15 Bearish FVG tapped by M5. Awaiting M5 IFVG.", category="BIAS_IFVG")
                        break
            
            # Look for an opposing M5 FVG that forms DURING the reaction
            if state["status"] == "AWAIT_IFVG_SETUP":
                for fvg in reversed(m5_fvgs):
                    fvg_time = fvg.get("index", pd.Timestamp.min)
                    if fvg_time >= state.get("manipulation_leg_start", pd.Timestamp.min):
                        if state["bias"] == "BUY" and fvg["type"] == "BEARISH":
                            state["m5_fvg_to_invert"] = fvg
                            state["status"] = "AWAIT_IFVG_CLOSE"
                            lookback = candles.iloc[-20:]
                            state["m5_swing_point"] = lookback["low"].min()
                            break
                        elif state["bias"] == "SELL" and fvg["type"] == "BULLISH":
                            state["m5_fvg_to_invert"] = fvg
                            state["status"] = "AWAIT_IFVG_CLOSE"
                            lookback = candles.iloc[-20:]
                            state["m5_swing_point"] = lookback["high"].max()
                            break

            if state["status"] == "AWAIT_IFVG_CLOSE":
                # Session filter blocks entries, but doesn't delete active setup
                if not self._is_within_session(current_time):
                    return None
                    
                if state["trades_today"] >= self.params.max_trades_per_day:
                    return None
                    
                fvg = state["m5_fvg_to_invert"]
                triggered = False
                
                # Check for body close through the opposing FVG
                if state["bias"] == "BUY" and latest["close"] > fvg["top"]:
                    triggered = True
                elif state["bias"] == "SELL" and latest["close"] < fvg["bottom"]:
                    triggered = True
                    
                # Invalidate if price breaks the swing extreme before inversion
                if state["bias"] == "BUY" and latest["close"] < state.get("m5_swing_point", 0):
                    state["status"] = "AWAIT_KEY_LEVEL"
                    self.log_event(f"[{symbol}] Bias IFVG setup failed (Swing low broken).", category="BIAS_IFVG")
                    return None
                elif state["bias"] == "SELL" and latest["close"] > state.get("m5_swing_point", float('inf')):
                    state["status"] = "AWAIT_KEY_LEVEL"
                    self.log_event(f"[{symbol}] Bias IFVG setup failed (Swing high broken).", category="BIAS_IFVG")
                    return None

                if triggered:
                    entry = latest["close"]
                    sl = state.get("m5_swing_point", entry * 0.99 if state["bias"]=="BUY" else entry * 1.01)
                    
                    # 1:2 RR target by default
                    if state["bias"] == "BUY":
                        tp = entry + (entry - sl) * 2.0
                    else:
                        tp = entry - (sl - entry) * 2.0
                        
                    state["status"] = "AWAIT_KEY_LEVEL"
                    state["trades_today"] += 1
                    
                    return TradeSignal(
                        strategy_id="BiasIFVG_v1",
                        symbol=symbol,
                        direction=state["bias"],
                        timeframe="M5",
                        entry_price=entry,
                        stop_loss=sl,
                        take_profit=tp,
                        confluence_score=85,
                        timestamp=float(latest.get("time", current_time.timestamp())),
                        metadata={"setup": "BIAS_IFVG"}
                    )

        return None
