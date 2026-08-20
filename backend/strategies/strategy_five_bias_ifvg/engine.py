import pandas as pd
import pytz

from backend.core.config_schema import UserConfigV2
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.fvg import FVGDetector
from backend.strategies.core.swing_structure import calculate_atr
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
                "wins_today": 0,
                "losses_today": 0,
                "extra_levels": []
            }
            self.htf_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.1)
            self.m15_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.1)
            self.m5_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.05)

    def notify_outcome(self, symbol: str, group_id: str, is_win: bool, pnl: float) -> None:
        """
        Called by the backtester/live engine after a full trade group closes.
        Tracks wins_today/losses_today so the spec's mandatory day-stop rule
        (stop after 1 win; after 1 loss only take a 2nd trade if it's A+; stop
        after 2 losses regardless) can be enforced by _can_trade_today().
        """
        self._init_state(symbol)
        state = self.state[symbol]
        if is_win:
            state["wins_today"] = state.get("wins_today", 0) + 1
            self.log_event(
                f"[{symbol}] BiasIFVG win recorded. Day-stop rule: no more trades today.",
                category="BIAS_IFVG",
            )
        else:
            state["losses_today"] = state.get("losses_today", 0) + 1
            self.log_event(
                f"[{symbol}] BiasIFVG loss recorded. losses_today={state['losses_today']}",
                category="BIAS_IFVG",
            )

    def _can_trade_today(self, state: dict, candidate_confluence: int | None = None) -> bool:
        """
        Spec day-stop rule: stop for the day after 1 win; after 1 loss, only take a
        2nd trade if it's a clearly A+ setup at a new key level; stop entirely after
        2 losses regardless.
        """
        wins = state.get("wins_today", 0)
        losses = state.get("losses_today", 0)
        if wins >= 1:
            return False
        if losses >= 2:
            return False
        if losses == 1:
            threshold = getattr(self.params, "a_plus_confluence_threshold", 85)
            if candidate_confluence is None or candidate_confluence < threshold:
                return False
        return True

    def _detect_cisd_and_rejections(self, candles: pd.DataFrame, bias: str) -> list:
        if len(candles) < 5 or bias is None:
            return []

        levels = []
        # `candles` passed into on_bar already has the unclosed bar stripped
        # upstream, so iloc[-1] IS the most recently completed candle — using
        # iloc[-2] here evaluated the pattern one bar late.
        latest = candles.iloc[-1]
        body_size = abs(latest["close"] - latest["open"])
        total_size = latest["high"] - latest["low"]

        min_body = 0.0
        min_body_atr_mult = getattr(self.params, "rejection_min_body_atr_mult", 0.0)
        if min_body_atr_mult > 0:
            atr = calculate_atr(candles)
            min_body = min_body_atr_mult * atr

        if total_size > 0 and body_size >= min_body:
            if bias == "BUY":
                lower_wick = min(latest["close"], latest["open"]) - latest["low"]
                if lower_wick > body_size * 2:
                    levels.append({
                        "type": "BULLISH_REJECTION",
                        "top": min(latest["close"], latest["open"]),
                        "bottom": latest["low"],
                        "time": candles.index[-1]
                    })
            elif bias == "SELL":
                upper_wick = latest["high"] - max(latest["close"], latest["open"])
                if upper_wick > body_size * 2:
                    levels.append({
                        "type": "BEARISH_REJECTION",
                        "top": latest["high"],
                        "bottom": max(latest["close"], latest["open"]),
                        "time": candles.index[-1]
                    })

        # CISD: a variable-length run (>= 2, uncapped) of same-direction-close
        # candles immediately preceding the reversal candle, per spec's "run of
        # down/up-close candles" language — not hardcoded to exactly 2 candles.
        # Scan backward from the candle before the reversal candle (`latest`)
        # while candles keep closing in the run direction.
        if len(candles) >= 4:
            reversal = latest
            run_is_down_close = bias == "BUY"
            run = []
            i = len(candles) - 2  # candle immediately before the reversal candle
            while i >= 0:
                c = candles.iloc[i]
                is_run_candle = (c["close"] < c["open"]) if run_is_down_close else (c["close"] > c["open"])
                if not is_run_candle:
                    break
                run.append(c)
                i -= 1

            if len(run) >= 2:
                # run[-1] is the oldest candle in the run — its open is the run's
                # "opening price" per spec, i.e. the CISD line.
                cisd_line = run[-1]["open"]
                if bias == "BUY" and reversal["close"] > cisd_line:
                    buffer = (reversal["close"] - cisd_line) * 0.1
                    levels.append({
                        "type": "BULLISH_CISD",
                        "top": cisd_line + buffer,
                        "bottom": cisd_line - buffer,
                        "time": candles.index[-1]
                    })
                elif bias == "SELL" and reversal["close"] < cisd_line:
                    buffer = (cisd_line - reversal["close"]) * 0.1
                    levels.append({
                        "type": "BEARISH_CISD",
                        "top": cisd_line + buffer,
                        "bottom": cisd_line - buffer,
                        "time": candles.index[-1]
                    })
        return levels

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
                "wins_today": 0,
                "losses_today": 0,
                "extra_levels": []
            }
            state = self.state[symbol]
            self.last_trade_date[symbol] = current_date
            self.log_event(f"[{symbol}] State reset for new trading day.", category="BIAS_IFVG")
        
        # 1. Determine Bias (HTF: H4) — always update on each H4 bar regardless of status.
        # Spec: "15m secondary check can flip bias intraday" — bias must be re-evaluated continuously.
        if timeframe == "H4":
            htf_fvgs = self.htf_detectors[symbol].update(candles)
            if htf_fvgs:
                last_fvg = htf_fvgs[-1]
                new_bias = "BUY" if last_fvg["type"] == "BULLISH" else "SELL"
                if new_bias != state["bias"]:
                    self.log_event(f"[{symbol}] HTF Bias flipped: {state['bias']} → {new_bias}", category="BIAS_IFVG")
                    # Bias flip invalidates any in-progress setup — restart from key-level search.
                    state["bias"] = new_bias
                    state["key_level"] = None
                    state["m5_fvg_to_invert"] = None
                    state["m5_swing_point"] = None
                    state["manipulation_leg_start"] = None
                    state["status"] = "AWAIT_KEY_LEVEL"
                elif state["status"] == "AWAIT_BIAS":
                    # First time bias is established
                    state["bias"] = new_bias
                    state["status"] = "AWAIT_KEY_LEVEL"
                    self.log_event(f"[{symbol}] Bias established: {state['bias']} based on HTF FVG", category="BIAS_IFVG")
        
        # 2. Key Levels (M15 tracker update)
        elif timeframe == "M15":
            self.m15_detectors[symbol].update(candles)
            if state["bias"] is not None:
                new_extra = self._detect_cisd_and_rejections(candles, state["bias"])
                if new_extra:
                    if "extra_levels" not in state:
                        state["extra_levels"] = []
                    state["extra_levels"].extend(new_extra)
                    # Keep only the latest 10 to avoid bloat
                    state["extra_levels"] = state["extra_levels"][-10:]

        # 3. IFVG Confirmation and Entry (M5)
        elif timeframe == "M5":
            m5_fvgs = []
            
            # Update M5 FVGs
            if state["status"] in ["AWAIT_IFVG_SETUP", "AWAIT_IFVG_CLOSE"]:
                m5_fvgs = self.m5_detectors[symbol].update(candles)
                
            # Check for M15 Tap (Real-time detection using M5 candle).
            # Only run while still searching for the key level itself — running this
            # while already AWAIT_IFVG_SETUP kept re-firing on every bar price dwells
            # near the zone, repeatedly pushing manipulation_leg_start forward and
            # starving the subsequent IFVG search of ever finding a qualifying gap.
            if state["status"] == "AWAIT_KEY_LEVEL":
                # Check FVGs
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

                # Check Extra Levels (CISD / Rejections)
                if state["status"] == "AWAIT_KEY_LEVEL":
                    for lvl in reversed(state.get("extra_levels", [])):
                        if state["bias"] == "BUY" and lvl["type"] in ["BULLISH_CISD", "BULLISH_REJECTION"] and lvl["bottom"] <= latest["low"] <= lvl["top"]:
                            state["key_level"] = lvl
                            state["status"] = "AWAIT_IFVG_SETUP"
                            state["manipulation_leg_start"] = current_time
                            self.log_event(f"[{symbol}] M15 {lvl['type']} tapped by M5. Awaiting M5 IFVG.", category="BIAS_IFVG")
                            break
                        elif state["bias"] == "SELL" and lvl["type"] in ["BEARISH_CISD", "BEARISH_REJECTION"] and lvl["bottom"] <= latest["high"] <= lvl["top"]:
                            state["key_level"] = lvl
                            state["status"] = "AWAIT_IFVG_SETUP"
                            state["manipulation_leg_start"] = current_time
                            self.log_event(f"[{symbol}] M15 {lvl['type']} tapped by M5. Awaiting M5 IFVG.", category="BIAS_IFVG")
                            break
            
            # Look for an opposing M5 FVG that forms DURING the reaction.
            # NOTE (unresolved product ambiguity — intentionally left as-is): this
            # searches for gaps forming AFTER the key-level tap. The spec's
            # "manipulation leg" language can also be read as describing the leg that
            # APPROACHED the level (i.e. before the tap). Re-aligning either the doc's
            # wording or this scan direction needs a product decision; not changed here.
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
                # Spec's mandatory day-stop rule (stop after 1 win; after 1 loss only
                # take a 2nd trade if it's A+; stop after 2 losses regardless).
                # confluence_score is currently a fixed 85 (see TradeSignal below);
                # checked against a_plus_confluence_threshold as the "A+" gate.
                if not self._can_trade_today(state, candidate_confluence=85):
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
                    
                    # Use configurable RR target (default 2.0)
                    target_rr = getattr(self.params, 'target_rr', 2.0)
                    if state["bias"] == "BUY":
                        tp = entry + (entry - sl) * target_rr
                    else:
                        tp = entry - (sl - entry) * target_rr
                        
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
