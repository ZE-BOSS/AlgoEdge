"""
backend/strategies/strategy_two/engine.py

CrashBoom Strategy Orchestrator
Source: CrashBoom_Strategy_Spec.md

Implements Continuous Drift + Discrete Jump logic.
Uses simplified empirical gap counting.
"""

from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np
from datetime import datetime
from backend.strategies.base_strategy import BaseStrategy, TradeSignal, TradeAction
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger
from backend.risk.position_sizer import get_pip_size
from backend.strategies.core.market_structure import MarketStructureDetector
from backend.services.bot_service import bot_service

logger = get_logger(__name__)

# Spec default values
SPEC_DEFAULTS = {
    "min_ema_separation_atr_multiple": 0.2,
    "pullback_max_distance_atr_multiple": 1.0,
    "confirmation_candles_required": 1,
    "atr_period": 14,
    "trailing_atr_multiple": 2.0,
    "size_floor_pct_of_normal": 25,
    "flatten_all_at_percentile": 99,
    "gap_percentile_hard_reduce": 90,
    "size_reduction_pct_at_hard_threshold": 50,
    "min_bars_before_trusting_fit": 100, # reduced for backtesting speed
}

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

@register_strategy("CrashBoom_v1")
class CrashBoomEngine(BaseStrategy):
    """
    Core engine for trading Synthetic Indices (Crash and Boom).
    Uses continuous drift continuation (EMA + pullback) and empirical gap sizing.
    """

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, 'crashboom', None)
        self.context: Dict[str, Any] = {}
        
        # State tracking
        self.last_jump_idx: Optional[int] = None
        self.jump_distances: List[int] = []
        self.ms_detector = MarketStructureDetector(swing_length=5, min_bos_count=1)
        self.post_jump_regime_reset = False

    def log_event(self, message: str, level: str = "INFO", category: str = "CBOOM"):
        """Intercept logs and send to bot_service."""
        from datetime import timezone
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

    async def initialize(self):
        logger.info("CrashBoomEngine initialized")

    def get_drift_direction(self, symbol: str) -> str:
        """Crash drifts UP (trade long). Boom drifts DOWN (trade short)."""
        symbol_upper = symbol.upper()
        if "CRASH" in symbol_upper:
            return "UP"
        elif "BOOM" in symbol_upper:
            return "DOWN"
        return "UP" # Default fallback

    def get_required_timeframes(self) -> List[str]:
        return ["M5"]

    def detect_jump(self, bar: pd.Series, symbol: str, atr_val: float) -> bool:
        """Detect if the bar is a massive jump against the drift."""
        symbol_upper = symbol.upper()
        
        # M9: use spike_lookback_bars and recovery_target_pips
        # C6: Use ATR-based dynamic jump detection, unless spike_threshold_pips is configured
        if pd.isna(atr_val) or atr_val <= 0:
            return False
            
        pip_size = get_pip_size(symbol)
        threshold_price = 4.0 * atr_val
        if self.params and getattr(self.params, 'spike_threshold_pips', 0) > 0:
            threshold_price = self.params.spike_threshold_pips * pip_size
            
        if abs(bar['open'] - bar['close']) < threshold_price:
            return False
            
        if "CRASH" in symbol_upper and bar['close'] < bar['open']:
            return True # Crash jump is down
        if "BOOM" in symbol_upper and bar['close'] > bar['open']:
            return True # Boom jump is up
            
        return False

    def compute_gap_percentile(self, bars_since: int) -> float:
        if not self.jump_distances or len(self.jump_distances) < 5:
            return 0.0 # Untrusted
        
        # What % of historical jumps happened AT OR BEFORE this many bars?
        sorted_dists = sorted(self.jump_distances)
        count_below = sum(1 for d in sorted_dists if d <= bars_since)
        return (count_below / len(sorted_dists)) * 100.0

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        """
        CrashBoom Strategy 1 + 2 evaluation.
        """
        if timeframe != "M5":
            return None
            
        # Clear run_logs for the new bar evaluation if not backtesting
        if not self.is_backtesting:
            self.run_logs = []
            
        if len(candles) < 50:
            return None

        current_bar = candles.iloc[-1]
        self.log_event(f"[{symbol}] Evaluating new {timeframe} bar: close={current_bar['close']}")

        # Load UI Params (M8: default to 20/50)
        fast_period = self.params.drift_ema_fast if getattr(self, 'params', None) else 20
        slow_period = self.params.drift_ema_slow if self.params else 50
        
        pip_size = get_pip_size(symbol)
        
        # Precompute indicators
        df = candles.copy()
        df['ema_fast'] = df['close'].ewm(span=fast_period, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=slow_period, adjust=False).mean()
        df['atr'] = calculate_atr(df, SPEC_DEFAULTS['atr_period'])
        
        # C5: NaN Guard on ATR
        atr_val = current_bar['atr']
        if pd.isna(atr_val) or atr_val <= 0:
            return None
            
        # Update Market Structure for M5 and M6
        ms_state = self.ms_detector.update(candles)

        # Detect historical jumps to build empirical distribution
        # C7/C8: Clear and re-evaluate cleanly without using absolute indices
        self.jump_distances = []
        last_j_idx = None
        for i in range(1, len(df)-1):
            b = df.iloc[i]
            if self.detect_jump(b, symbol, b['atr']):
                if last_j_idx is not None:
                    self.jump_distances.append(i - last_j_idx)
                last_j_idx = i
                
        self.last_jump_idx = last_j_idx
            
        # Check if CURRENT bar is a jump
        if self.detect_jump(current_bar, symbol, atr_val):
            if self.last_jump_idx is not None:
                dist = (len(df) - 1) - self.last_jump_idx
                self.jump_distances.append(dist)
            self.last_jump_idx = len(df) - 1
            self.post_jump_regime_reset = True  # M7
            self.log_event(f"Jump detected! Resetting regime wait.")
            return None
            
        bars_since_jump = (len(df) - 1 - self.last_jump_idx) if self.last_jump_idx is not None else 999999
        
        # M1: 5-bar cooldown
        if bars_since_jump < 5:
            self.log_event(f"In 5-bar jump cooldown ({bars_since_jump} bars).")
            return None
        gap_pct = self.compute_gap_percentile(bars_since_jump)
        self.log_event(f"Gap percentile computed: {gap_pct:.1f}%")
        
        # Strategy 2: Jump Exposure Management
        if gap_pct >= SPEC_DEFAULTS['flatten_all_at_percentile']:
            # Signal a flatten (close all) if implemented in orchestrator, or just block entries
            # For now, block new entries.
            self.log_event(f"Gap percentile {gap_pct:.1f}% >= threshold {SPEC_DEFAULTS['flatten_all_at_percentile']}. Blocking entries.")
            return None
            
        # Strategy 1: Drift Continuation
        drift_dir = self.get_drift_direction(symbol)
        
        # Regime Filter
        ema_sep = abs(current_bar['ema_fast'] - current_bar['ema_slow'])
        min_sep = SPEC_DEFAULTS['min_ema_separation_atr_multiple'] * atr_val
        
        if drift_dir == "UP":
            regime_active = current_bar['ema_fast'] > current_bar['ema_slow'] and ema_sep > min_sep
        else:
            regime_active = current_bar['ema_fast'] < current_bar['ema_slow'] and ema_sep > min_sep
            
        if not regime_active:
            self.post_jump_regime_reset = False # Allow reset if regime dies
            self.log_event(f"Regime {drift_dir} inactive (EMA separation {ema_sep:.5f} < {min_sep:.5f} or crossed)")
            return None
            
        # M7: Post-Jump Regime Reset
        if self.post_jump_regime_reset:
            # We must wait for the regime to re-qualify, meaning a ChoCH in the drift direction
            if drift_dir == "UP" and ms_state.get("last_choch") != "BULLISH":
                self.log_event("Waiting for BULLISH ChoCH to reset regime after jump.")
                return None
            elif drift_dir == "DOWN" and ms_state.get("last_choch") != "BEARISH":
                self.log_event("Waiting for BEARISH ChoCH to reset regime after jump.")
                return None
            self.post_jump_regime_reset = False
            self.log_event("Post-jump regime reset complete.")
            
        # Entry Trigger: Pullback to fast EMA
        dist_to_fast = abs(current_bar['close'] - current_bar['ema_fast'])
        max_dist = SPEC_DEFAULTS['pullback_max_distance_atr_multiple'] * atr_val
        
        if dist_to_fast > max_dist:
            self.log_event(f"Pullback dist {dist_to_fast:.5f} > max_dist {max_dist:.5f}. Not entered.")
            return None # Not pulled back enough, or too far
            
        # M6: Confirmation - Require close beyond prior swing AND drift direction
        swings = ms_state.get("swings", [])
        if not swings: 
            self.log_event("No swings detected for confirmation.")
            return None
        
        if drift_dir == "UP":
            if current_bar['close'] <= current_bar['open']: 
                self.log_event("Bullish drift requires bullish close candle.")
                return None
            # Must close above previous swing high
            highs = [s for s in swings if s["type"] == "HIGH" and float(s["price"]) < current_bar["close"]]
            if not highs: 
                self.log_event("No previous swing high breached.")
                return None
        else:
            if current_bar['close'] >= current_bar['open']: 
                self.log_event("Bearish drift requires bearish close candle.")
                return None
            # Must close below previous swing low
            lows = [s for s in swings if s["type"] == "LOW" and float(s["price"]) > current_bar["close"]]
            if not lows: 
                self.log_event("No previous swing low breached.")
                return None

        self.log_event(f"M6 Confirmation passed! Building signal...")

        # Build Signal
        direction_str = "BUY" if drift_dir == "UP" else "SELL"
        entry_price = float(current_bar['close'])
        
        # Sizing modifier based on Gap Percentile
        size_modifier = 1.0
        if gap_pct >= SPEC_DEFAULTS['gap_percentile_hard_reduce']:
            size_modifier = 1.0 - (SPEC_DEFAULTS['size_reduction_pct_at_hard_threshold'] / 100.0)
            
        # M5: Stop Loss - Structure based
        buffer = 1.5 * atr_val
        
        if direction_str == "BUY":
            lows = [s for s in swings if s["type"] == "LOW" and float(s["price"]) < entry_price]
            sl = float(lows[-1]["price"]) - buffer if lows else entry_price - (atr_val * SPEC_DEFAULTS['trailing_atr_multiple'])
        else:
            highs = [s for s in swings if s["type"] == "HIGH" and float(s["price"]) > entry_price]
            sl = float(highs[-1]["price"]) + buffer if highs else entry_price + (atr_val * SPEC_DEFAULTS['trailing_atr_multiple'])
        
        # M3: Minimum RR Gate
        risk = abs(entry_price - sl)
        min_reward = risk * 1.5
        atr_dist = atr_val * SPEC_DEFAULTS['trailing_atr_multiple']
        
        recovery_pips = self.params.recovery_target_pips if self.params and getattr(self.params, 'recovery_target_pips', 0) > 0 else 0
        recovery_dist = recovery_pips * get_pip_size(symbol)
        
        if direction_str == "BUY":
            tp = entry_price + max(atr_dist * 5, min_reward, recovery_dist)
        else:
            tp = entry_price - max(atr_dist * 5, min_reward, recovery_dist)
        
        sig = TradeSignal(
            strategy_id="CrashBoom_v1",
            symbol=symbol,
            direction=direction_str,
            signal_type="PULLBACK_ENTRY",
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            confluence_score=80,
            timeframe=timeframe,
            metadata={
                "size_modifier": size_modifier,
                "gap_pct": gap_pct,
                "trail_method": "ATR_TRAIL",
                "atr_val": float(atr_val),
                "reason": f"CrashBoom Drift {drift_dir}. Gap Pct: {gap_pct:.1f}%"
            }
        )
        
        return sig

    async def on_tick(self, symbol: str, tick: Dict[str, Any]) -> None:
        pass
