"""
backend/strategies/smc/market_structure.py

Market structure detection: Swings, Break of Structure (BOS), Change of Character (ChoCH).
Source: SMC_Strategy.md Section 1
"""

import pandas as pd
from typing import Dict, Any

class MarketStructureDetector:
    """Detects HH, HL, LH, LL and structural breaks."""

    def __init__(self, swing_length: int = 5):
        self.swing_length = swing_length
        self.swings = []
        self.trend = "NEUTRAL"

    def update(self, candles: pd.DataFrame) -> Dict[str, Any]:
        """
        Process new candles to find swings and structural breaks.
        """
        if len(candles) < self.swing_length * 2 + 1:
            return {"trend": self.trend, "last_bos": None, "last_choch": None, "swings": self.swings}

        # Calculate local extremums for swings
        highs = candles['high'].rolling(window=self.swing_length*2+1, center=True).max()
        is_swing_high = candles['high'] == highs
        
        lows = candles['low'].rolling(window=self.swing_length*2+1, center=True).min()
        is_swing_low = candles['low'] == lows

        self.swings = []
        # Rebuild swings
        for i in range(len(candles)):
            # Ignore the edges where rolling window is NaN
            if pd.isna(highs.iloc[i]): continue
            
            if is_swing_high.iloc[i]:
                self.swings.append({"type": "HIGH", "price": candles['high'].iloc[i], "index": candles.index[i]})
            if is_swing_low.iloc[i]:
                self.swings.append({"type": "LOW", "price": candles['low'].iloc[i], "index": candles.index[i]})

        last_bos = None
        last_choch = None

        high_swings = [s for s in self.swings if s["type"] == "HIGH"]
        low_swings = [s for s in self.swings if s["type"] == "LOW"]

        if len(high_swings) >= 2 and len(low_swings) >= 2:
            hh = high_swings[-1]["price"] > high_swings[-2]["price"]
            hl = low_swings[-1]["price"] > low_swings[-2]["price"]
            lh = high_swings[-1]["price"] < high_swings[-2]["price"]
            ll = low_swings[-1]["price"] < low_swings[-2]["price"]

            if hh and hl:
                if self.trend == "BEARISH": last_choch = "BULLISH"
                else: last_bos = "BULLISH"
                self.trend = "BULLISH"
            elif lh and ll:
                if self.trend == "BULLISH": last_choch = "BEARISH"
                else: last_bos = "BEARISH"
                self.trend = "BEARISH"
        return {
            "trend": self.trend,
            "last_bos": last_bos,
            "last_choch": last_choch,
            "swings": self.swings
        }

    def get_bias(self) -> str:
        """Return the current directional bias (BULLISH/BEARISH/NEUTRAL)."""
        return self.trend
