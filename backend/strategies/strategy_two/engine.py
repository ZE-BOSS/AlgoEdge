"""
backend/strategies/strategy_two/engine.py

DriftJumpAlpha Strategy Orchestrator
Source: DriftJumpAlpha_Strategy_Spec_v2.md

Implements Continuous Drift (Setup A) + Discrete Jump (Setup B) logic for Crash indices only.
Uses simplified empirical gap counting.
"""

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from backend.risk.position_sizer import get_pip_size
from backend.services.bot_service import bot_service
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.market_structure import MarketStructureDetector
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Spec default values for DriftJumpAlpha v2
SPEC_DEFAULTS = {
    "min_ema_separation_atr_multiple": 0.2,
    "pullback_max_distance_atr_multiple": 1.0,
    "confirmation_candles_required": 1,
    "atr_period": 14,
    "trailing_atr_multiple_low_vol": 1.5,
    "trailing_atr_multiple_high_vol": 2.5,
    "flatten_all_at_percentile": 99,
    "gap_percentile_hard_reduce": 90,
    "size_reduction_pct_at_hard_threshold": 50,
    "min_bars_before_trusting_fit": 100,
    "min_adx_to_trade": 20,
    "jump_entry_percentile_threshold": 95.0,
}

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate ADX (Average Directional Index) using pandas."""
    # Compute +DM and -DM
    up_move = df['high'] - df['high'].shift(1)
    down_move = df['low'].shift(1) - df['low']
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    tr = calculate_atr(df, 1) # True range
    atr = tr.rolling(window=period).mean()
    
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(window=period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(window=period).mean() / atr)
    
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(window=period).mean()
    return adx

@register_strategy("DriftJumpAlpha_v1")
class DriftJumpAlphaEngine(BaseStrategy):
    """
    Core engine for trading Crash Synthetic Indices.
    Setup A: Drift Continuation (Buy)
    Setup B: Jump Entry (Sell)
    """

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, 'drift_jump_alpha', getattr(config, 'crashboom', None))
        self.context: dict[str, Any] = {}
        
        # State tracking
        self.last_jump_idx: int | None = None
        self.jump_distances: list[int] = []
        self.ms_detector = MarketStructureDetector(swing_length=5, min_bos_count=1)
        self.post_jump_regime_reset = False

    def log_event(self, message: str, level: str = "INFO", category: str = "DJA"):
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
        logger.info("DriftJumpAlphaEngine initialized")

    def get_required_timeframes(self) -> list[str]:
        return ["M5"]

    def detect_jump(self, bar: pd.Series, symbol: str, atr_val: float) -> bool:
        """Detect if the bar is a massive jump against the drift (downwards for Crash)."""
        symbol_upper = symbol.upper()
        
        if pd.isna(atr_val) or atr_val <= 0:
            return False
            
        pip_size = get_pip_size(symbol)
        threshold_price = 4.0 * atr_val
        if self.params and getattr(self.params, 'spike_threshold_pips', 0) > 0:
            threshold_price = self.params.spike_threshold_pips * pip_size
            
        if abs(bar['open'] - bar['close']) < threshold_price:
            return False
            
        # Hard Crash-only jump logic (downwards)
        if "CRASH" in symbol_upper and bar['close'] < bar['open']:
            return True 
            
        return False

    def compute_gap_percentile(self, bars_since: int) -> float:
        if not self.jump_distances or len(self.jump_distances) < 5:
            return 0.0 # Untrusted
        
        sorted_dists = sorted(self.jump_distances)
        count_below = sum(1 for d in sorted_dists if d <= bars_since)
        return (count_below / len(sorted_dists)) * 100.0

    def calculate_adaptive_atr_multiple(self, current_atr: float, avg_atr: float) -> float:
        """Adaptive trailing ATR multiple based on recent volatility vs avg volatility."""
        if current_atr > (avg_atr * 1.2):
            return SPEC_DEFAULTS['trailing_atr_multiple_high_vol']
        return SPEC_DEFAULTS['trailing_atr_multiple_low_vol']

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        """
        DriftJumpAlpha Setup A (Drift) + Setup B (Jump Entry) evaluation.
        """
        if timeframe != "M5":
            return None
            
        if "CRASH" not in symbol.upper():
            return None # HARD CRASH ONLY FILTER
            
        if not self.is_backtesting:
            self.run_logs = []
            
        if len(candles) < 50:
            return None

        current_bar = candles.iloc[-1]
        self.log_event(f"[{symbol}] Evaluating new {timeframe} bar: close={current_bar['close']}")

        # Load UI Params
        fast_period = self.params.drift_ema_fast if getattr(self, 'params', None) else 20
        slow_period = self.params.drift_ema_slow if self.params else 50
        min_adx = getattr(self.params, 'min_adx_to_trade', SPEC_DEFAULTS['min_adx_to_trade'])
        jump_threshold = getattr(self.params, 'jump_entry_percentile_threshold', SPEC_DEFAULTS['jump_entry_percentile_threshold'])
        trade_jumps = getattr(self.params, 'trade_jumps_enabled', False)
        control_test_passed = getattr(self.params, 'control_test_passed', False)
        
        pip_size = get_pip_size(symbol)
        
        # Precompute indicators
        df = candles.copy()
        df['ema_fast'] = df['close'].ewm(span=fast_period, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=slow_period, adjust=False).mean()
        df['atr'] = calculate_atr(df, SPEC_DEFAULTS['atr_period'])
        df['adx'] = calculate_adx(df, 14)
        
        current_bar = df.iloc[-1]
        atr_val = current_bar['atr']
        adx_val = current_bar['adx']
        avg_atr = df['atr'].mean()
        
        if pd.isna(atr_val) or atr_val <= 0:
            return None
            
        # Update Market Structure
        ms_state = self.ms_detector.update(candles)

        # Detect historical jumps only once
        if not getattr(self, 'history_scanned', False):
            self.jump_distances = []
            last_j_idx = None
            for i in range(1, len(df)-1):
                b = df.iloc[i]
                if self.detect_jump(b, symbol, b['atr']):
                    if last_j_idx is not None:
                        self.jump_distances.append(i - last_j_idx)
                    last_j_idx = i
                    
            self.last_jump_idx = last_j_idx
            self.history_scanned = True
            
        if self.detect_jump(current_bar, symbol, atr_val):
            if self.last_jump_idx is not None:
                dist = (len(df) - 1) - self.last_jump_idx
                self.jump_distances.append(dist)
            self.last_jump_idx = len(df) - 1
            self.post_jump_regime_reset = True
            self.log_event("Jump detected! Resetting regime wait.")
            return None
            
        bars_since_jump = (len(df) - 1 - self.last_jump_idx) if self.last_jump_idx is not None else 999999
        
        if bars_since_jump < 5:
            self.log_event(f"In 5-bar jump cooldown ({bars_since_jump} bars).")
            return None
            
        gap_pct = self.compute_gap_percentile(bars_since_jump)
        self.log_event(f"Gap percentile computed: {gap_pct:.1f}%")
        
        if gap_pct >= SPEC_DEFAULTS['flatten_all_at_percentile']:
            self.log_event(f"Gap percentile {gap_pct:.1f}% >= threshold {SPEC_DEFAULTS['flatten_all_at_percentile']}. Blocking entries.")
            return None

        # ---------------------------------------------------------
        # SETUP B: JUMP ENTRY (SELL)
        # ---------------------------------------------------------
        if trade_jumps and control_test_passed and gap_pct >= jump_threshold:
            swings = ms_state.get("swings", [])
            if swings and current_bar['close'] < current_bar['open']:
                lows = [s for s in swings if s["type"] == "LOW" and float(s["price"]) > current_bar["close"]]
                if lows:
                    # Bearish ChoCH/break of swing low achieved!
                    self.log_event(f"Setup B (Jump Entry) Triggered! Gap={gap_pct:.1f}% >= {jump_threshold}%")
                    
                    entry_price = float(current_bar['close'])
                    highs = [s for s in swings if s["type"] == "HIGH" and float(s["price"]) > entry_price]
                    buffer = 0.2 * atr_val
                    sl = float(highs[-1]["price"]) + buffer if highs else entry_price + (atr_val * 1.5)
                    
                    risk = abs(entry_price - sl)
                    min_reward = risk * 1.5
                    tp = entry_price - max(atr_val * 3, min_reward)
                    
                    return TradeSignal(
                        strategy_id="DriftJumpAlpha_v1",
                        symbol=symbol,
                        direction="SELL",
                        signal_type="JUMP_ENTRY",
                        entry_price=entry_price,
                        stop_loss=sl,
                        take_profit=tp,
                        confluence_score=95,
                        timeframe=timeframe,
                        timestamp=candles.index[-1].timestamp(),
                        metadata={
                            "size_modifier": 1.0, # Sizing rules or ceilings applied at order execution layer
                            "gap_pct": gap_pct,
                            "trail_method": "NONE",
                            "atr_val": float(atr_val),
                            "setup": "setup_b_jump",
                            "reason": f"Jump SELL. Gap: {gap_pct:.1f}%",
                            "aggregate_max_lots_per_symbol": getattr(self.params, 'aggregate_max_lots_per_symbol', 0.0)
                        }
                    )
            
        # ---------------------------------------------------------
        # SETUP A: DRIFT CONTINUATION (BUY)
        # ---------------------------------------------------------
        if gap_pct >= jump_threshold:
            self.log_event(f"Gap pct {gap_pct:.1f}% >= jump threshold {jump_threshold}%. Blocking Drift Buys.")
            return None

        ema_sep = abs(current_bar['ema_fast'] - current_bar['ema_slow'])
        min_sep = SPEC_DEFAULTS['min_ema_separation_atr_multiple'] * atr_val
        
        regime_active = current_bar['ema_fast'] > current_bar['ema_slow'] and ema_sep > min_sep
        
        if pd.notna(adx_val) and adx_val < min_adx:
            self.log_event(f"ADX {adx_val:.1f} < {min_adx}. Drift Regime ignored due to weak trend.")
            regime_active = False
            
        if not regime_active:
            self.post_jump_regime_reset = False
            self.log_event("Drift Regime UP inactive.")
            return None
            
        if self.post_jump_regime_reset:
            if ms_state.get("last_choch") != "BULLISH":
                self.log_event("Waiting for BULLISH ChoCH to reset regime after jump.")
                return None
            self.post_jump_regime_reset = False
            self.log_event("Post-jump regime reset complete.")
            
        # Pullback Trigger
        dist_to_fast = abs(current_bar['close'] - current_bar['ema_fast'])
        max_dist = SPEC_DEFAULTS['pullback_max_distance_atr_multiple'] * atr_val
        
        if dist_to_fast > max_dist:
            return None
            
        # M6 Confirmation
        swings = ms_state.get("swings", [])
        if not swings: 
            return None
        
        if current_bar['close'] <= current_bar['open']: 
            self.log_event("Bullish drift requires bullish close candle.")
            return None
            
        highs = [s for s in swings if s["type"] == "HIGH" and float(s["price"]) < current_bar["close"]]
        if not highs: 
            self.log_event("No previous swing high breached.")
            return None

        self.log_event("Setup A Confirmation passed! Building signal...")

        entry_price = float(current_bar['close'])
        
        size_modifier = 1.0
        if gap_pct >= SPEC_DEFAULTS['gap_percentile_hard_reduce']:
            size_modifier = 1.0 - (SPEC_DEFAULTS['size_reduction_pct_at_hard_threshold'] / 100.0)
            
        buffer = 1.5 * atr_val
        
        lows = [s for s in swings if s["type"] == "LOW" and float(s["price"]) < entry_price]
        sl = float(lows[-1]["price"]) - buffer if lows else entry_price - (atr_val * self.calculate_adaptive_atr_multiple(atr_val, avg_atr))
        
        risk = abs(entry_price - sl)
        min_reward = risk * 1.5
        atr_dist = atr_val * self.calculate_adaptive_atr_multiple(atr_val, avg_atr)
        
        recovery_pips = self.params.recovery_target_pips if self.params and getattr(self.params, 'recovery_target_pips', 0) > 0 else 0
        recovery_dist = recovery_pips * pip_size
        
        tp = entry_price + max(atr_dist * 5, min_reward, recovery_dist)
        
        sig = TradeSignal(
            strategy_id="DriftJumpAlpha_v1",
            symbol=symbol,
            direction="BUY",
            signal_type="PULLBACK_ENTRY",
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            confluence_score=80,
            timeframe=timeframe,
            timestamp=candles.index[-1].timestamp(),
            metadata={
                "size_modifier": size_modifier,
                "gap_pct": gap_pct,
                "trail_method": "ATR_TRAIL",
                "atr_val": float(atr_val),
                "setup": "setup_a_drift",
                "reason": f"Drift Continuation UP. Gap Pct: {gap_pct:.1f}%",
                "aggregate_max_lots_per_symbol": getattr(self.params, 'aggregate_max_lots_per_symbol', 0.0)
            }
        )
        
        return sig

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        pass
