"""
backend/strategies/smc/ipdm.py

Institutional Price Delivery Model (IPDM) phase detection using ATR.
Identifies Accumulation, Manipulation, and Expansion phases.
Source: SMC_Strategy.md Section 8
"""

import pandas as pd
from typing import Dict, Any

class IPDMDetector:
    """Tracks PO3 (Power of 3) cycle phases."""

    def __init__(self, accum_atr_ratio: float = 0.7, exp_atr_ratio: float = 1.2):
        self.accum_atr_ratio = accum_atr_ratio
        self.exp_atr_ratio = exp_atr_ratio
        self.current_phase = "UNKNOWN"

    def update(self, candles: pd.DataFrame) -> Dict[str, Any]:
        """Determine current IPDM phase based on ATR compression/expansion."""
        if len(candles) < 65: # 14 for ATR + 50 for baseline
            return {"phase": self.current_phase}
            
        high = candles['high']
        low = candles['low']
        close = candles['close']
        
        # True Range
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # 14-period ATR
        atr = tr.rolling(window=14).mean()
        
        # Long-term ATR baseline
        baseline_atr = atr.rolling(window=50).mean().iloc[-1]
        current_atr = atr.iloc[-1]
        
        if pd.isna(baseline_atr) or pd.isna(current_atr) or baseline_atr == 0:
            return {"phase": self.current_phase}
            
        ratio = current_atr / baseline_atr
        
        if ratio < self.accum_atr_ratio:
            self.current_phase = "ACCUMULATION"
        elif ratio > self.exp_atr_ratio:
            self.current_phase = "EXPANSION"
        else:
            self.current_phase = "MANIPULATION"
            
        return {"phase": self.current_phase}
