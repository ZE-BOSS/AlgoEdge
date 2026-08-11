"""
backend/strategies/strategy_six_crt/engine.py

CRT (Candle Range Theory) Strategy Orchestrator
Source: CRT_Strategy_Spec.md
"""

from datetime import datetime, time
from typing import Any

import pandas as pd
import pytz

from backend.services.bot_service import bot_service
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.market_structure import MarketStructureDetector
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

SPEC_DEFAULTS = {
    "htf_timeframe": "H1",
    "ltf_timeframe": "M5",
    "target_r_multiple": 1.5,
    "max_trades_per_session": 1,
    "session_start": "09:30",
    "session_cutoff": "12:00"
}

@register_strategy("CRT_v1")
class CRTEngine(BaseStrategy):
    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, 'crt', None)
        self.context: dict[str, Any] = {}
        self.ms_detector = MarketStructureDetector(swing_length=5, min_bos_count=1)
        
        # State tracking
        self.c1: dict[str, float] | None = None
        self.c2_trigger: dict[str, Any] | None = None
        self.trades_today = 0
        self.last_trade_date = None
        # Live bar-time deduplication: mirrors backtester's prev_time_by_tf guard.
        # Prevents the 60-second scan loop from re-presenting the same closed HTF
        # candle and triggering the c2_trigger timeout prematurely.
        self._last_htf_bar_time: dict[str, Any] = {}

    async def initialize(self):
        logger.info("CRTEngine initialized")

    def get_required_timeframes(self) -> list[str]:
        htf = self.params.htf_timeframe if getattr(self, 'params', None) else SPEC_DEFAULTS['htf_timeframe']
        ltf = self.params.ltf_timeframe if getattr(self, 'params', None) else SPEC_DEFAULTS['ltf_timeframe']
        return [htf, ltf]

    def _is_within_session(self, current_time: datetime, symbol: str) -> bool:
        """Check if current time is within NY session. Bypass for synthetics if configured."""
        # Bypass for synthetics (Crash/Boom/Volatility/Jump)
        is_synthetic = any(k in symbol.upper() for k in ["CRASH", "BOOM", "VOLATILITY", "JUMP", "STEP"])
        bypass_synthetics = self.params.bypass_session_synthetics if getattr(self, 'params', None) else True
        if is_synthetic and bypass_synthetics:
            return True
            
        ny_tz = pytz.timezone('America/New_York')
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize('UTC')
        ny_time = current_time.astimezone(ny_tz).time()
        
        start_str = self.params.session_start if getattr(self, 'params', None) else SPEC_DEFAULTS['session_start']
        cutoff_str = self.params.session_cutoff if getattr(self, 'params', None) else SPEC_DEFAULTS['session_cutoff']
        
        try:
            start = time(int(start_str.split(':')[0]), int(start_str.split(':')[1]))
            cutoff = time(int(cutoff_str.split(':')[0]), int(cutoff_str.split(':')[1]))
            return start <= ny_time <= cutoff
        except:
            return True

    def _get_htf_bias(self) -> str:
        """Use MS detector to get bias on HTF."""
        return self.ms_detector.get_bias()

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        if not self.is_backtesting:
            self.run_logs = []
            
        if len(candles) < 10:
            return None

        htf = self.params.htf_timeframe if getattr(self, 'params', None) else SPEC_DEFAULTS['htf_timeframe']
        ltf = self.params.ltf_timeframe if getattr(self, 'params', None) else SPEC_DEFAULTS['ltf_timeframe']

        current_bar = candles.iloc[-1]
        dt = current_bar.name if isinstance(current_bar.name, pd.Timestamp) else pd.to_datetime('now')
        
        if self.last_trade_date != dt.date():
            self.trades_today = 0
            self.last_trade_date = dt.date()
            self.ms_detector = MarketStructureDetector(swing_length=5, min_bos_count=1)
            self.c1 = None
            self.c2_trigger = None
            
        max_trades = self.params.max_trades_per_session if getattr(self, 'params', None) else SPEC_DEFAULTS['max_trades_per_session']
        if self.trades_today >= max_trades:
            return None
            
        in_session = self._is_within_session(dt, symbol)

        if timeframe == htf:
            # ── Bar-time deduplication (live mode) ──────────────────────────────
            # The live scan loop calls on_bar(H1) every 60s with the same closed
            # candle for an entire hour.  Guard against this so the c2_trigger
            # timeout only fires when a genuinely NEW H1 candle closes.
            _current_bar_ts = current_bar.name
            _last_htf_ts = self._last_htf_bar_time.get(htf)
            if _last_htf_ts is not None and _current_bar_ts == _last_htf_ts:
                return None  # Same H1 bar — preserve c2_trigger, skip HTF logic
            self._last_htf_bar_time[htf] = _current_bar_ts
            # ────────────────────────────────────────────────────────────────────

            # Update HTF market structure
            self.ms_detector.update(candles)
            
            # Evaluate C1/C2 logic
            if self.c1 is None:
                self.c1 = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                self.log_event(f"New C1 Candidate set on HTF: High {self.c1['high']}, Low {self.c1['low']}")
            else:
                if self.c2_trigger is not None:
                    # Trigger timeout - invalidate if a new HTF candle closes without LTF trigger firing
                    self.log_event("Trigger timeout - LTF did not fire before next HTF close. Invalidating setup.")
                    self.c2_trigger = None
                    self.c1 = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                    return None

                # We have a C1, check if this bar is a valid C2
                c1_high = self.c1["high"]
                c1_low = self.c1["low"]
                c2_high = float(current_bar['high'])
                c2_low = float(current_bar['low'])
                c2_close = float(current_bar['close'])
                
                # Check for ambiguous intrabar double sweep
                if c2_high > c1_high and c2_low < c1_low:
                    self.log_event("Ambiguous C2 (swept both sides of C1). Invalidating and setting new C1.")
                    self.c1 = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                    return None

                bias = self._get_htf_bias()
                
                if bias == "FLAT":
                    self.c1 = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                    return None

                valid_bullish = (c2_low < c1_low < c2_close < c1_high)
                valid_bearish = c2_high > c1_high and (c1_low < c2_close < c1_high)

                if valid_bullish and bias == "BULLISH":
                    if in_session:
                        self.log_event(f"Valid Bullish C2 sweep! HTF Bias: {bias}. Trigger level set to {c2_high}.")
                        self.c2_trigger = {"direction": "BUY", "level": c2_high, "c1_extreme": c1_high}
                    else:
                        self.log_event("Valid C2 but out of session. Ignoring.")
                        self.c1 = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                elif valid_bearish and bias == "BEARISH":
                    if in_session:
                        self.log_event(f"Valid Bearish C2 sweep! HTF Bias: {bias}. Trigger level set to {c2_low}.")
                        self.c2_trigger = {"direction": "SELL", "level": c2_low, "c1_extreme": c1_low}
                    else:
                        self.log_event("Valid C2 but out of session. Ignoring.")
                        self.c1 = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                else:
                    self.log_event("C2 did not match C1/bias criteria. Setting new C1 candidate.")
                    self.c1 = {"high": float(current_bar['high']), "low": float(current_bar['low'])}

        elif timeframe == ltf:
            # Monitor for trigger
            if self.c2_trigger is not None:
                current_price = float(current_bar['close'])
                direction = self.c2_trigger["direction"]
                trigger_lvl = self.c2_trigger["level"]
                tp = self.c2_trigger["c1_extreme"]
                
                triggered = False
                if (direction == "BUY" and current_price > trigger_lvl) or (direction == "SELL" and current_price < trigger_lvl):
                    triggered = True
                    
                if triggered:
                    self.log_event(f"CRT LTF Trigger fired! Executing {direction}.")
                    self.c2_trigger = None # Clear trigger
                    self.c1 = None
                    self.trades_today += 1
                    
                    target_r = self.params.target_r_multiple if getattr(self, 'params', None) else SPEC_DEFAULTS['target_r_multiple']
                    tp_dist = abs(tp - current_price)
                    # Per spec (CRT_Strategy_Spec.md Section 6): SL is derived backward from TP.
                    sl_dist = tp_dist / target_r

                    # ── Minimum SL floor (addition above spec) ──────────────────────
                    # Small C1 candles produce spec-correct but tiny SL distances,
                    # which cause extreme lot sizes in the risk engine.
                    # Apply configurable floors: min_sl_pips (hard pips minimum)
                    # and sl_atr_mult (fraction of recent ATR). Whichever is larger wins.
                    from backend.risk.position_sizer import get_pip_size
                    pip_size = get_pip_size(symbol)
                    min_sl_pips_val = getattr(self.params, 'min_sl_pips', 15.0) if self.params else 15.0
                    sl_atr_mult_val = getattr(self.params, 'sl_atr_mult', 1.0) if self.params else 1.0

                    min_sl_from_pips = min_sl_pips_val * pip_size

                    # Standard ATR (Average True Range) over 14 periods
                    try:
                        import numpy as np
                        if len(candles) >= 15:
                            highs = candles['high'].values[-14:].astype(float)
                            lows  = candles['low'].values[-14:].astype(float)
                            prev_closes = candles['close'].values[-15:-1].astype(float)
                            
                            tr1 = highs - lows
                            tr2 = np.abs(highs - prev_closes)
                            tr3 = np.abs(lows - prev_closes)
                            
                            true_range = np.maximum(tr1, np.maximum(tr2, tr3))
                            atr_approx = float(true_range.mean())
                        else:
                            atr_approx = tp_dist  # fallback: use TP dist as proxy
                    except Exception:
                        atr_approx = tp_dist

                    min_sl_from_atr = atr_approx * sl_atr_mult_val
                    # Apply the most restrictive floor (largest SL distance wins)
                    sl_dist = max(sl_dist, min_sl_from_pips, min_sl_from_atr)

                    # If flooring the SL makes the TP distance shorter than 1R, extend TP
                    # so the trade still meets the target_r ratio (keeps the edge intact).
                    if tp_dist < sl_dist * target_r:
                        if direction == "BUY":
                            tp = current_price + sl_dist * target_r
                        else:
                            tp = current_price - sl_dist * target_r
                    # ────────────────────────────────────────────────────────────────

                    if direction == "BUY":
                        sl = current_price - sl_dist
                    else:
                        sl = current_price + sl_dist

                        
                    return TradeSignal(
                        strategy_id="CRT_v1",
                        symbol=symbol,
                        direction=direction,
                        signal_type="BREAKOUT_ENTRY",
                        entry_price=current_price,
                        stop_loss=sl,
                        take_profit=tp,
                        confluence_score=90,
                        timeframe=ltf,
                        timestamp=candles.index[-1].timestamp(),
                        metadata={
                            "reason": "CRT Setup C1/C2. NY Session.",
                            "htf": htf
                        }
                    )

        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        pass
