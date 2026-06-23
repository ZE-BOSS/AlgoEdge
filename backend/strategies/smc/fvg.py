"""
backend/strategies/smc/fvg.py

Fair Value Gap (FVG) detection and Consequent Encroachment (CE) levels.
Source: SMC_Strategy.md Section 2
"""

import pandas as pd
from typing import List, Dict, Any

class FVGDetector:
    """Detects 3-candle Fair Value Gaps."""

    def __init__(self, min_gap_pips: float = 3.0):
        self.min_gap_pips = min_gap_pips
        self.active_fvgs = []

    def update(self, candles: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Scan for new FVGs and remove mitigated ones.
        """
        if len(candles) < 3:
            return self.active_fvgs

        # 1. Check for fills of existing FVGs
        latest = candles.iloc[-1]
        for fvg in self.active_fvgs[:]:
            if fvg["type"] == "BULLISH":
                if latest["low"] < fvg["top"]:
                    fvg["top"] = latest["low"]
                if fvg["top"] <= fvg["bottom"]:
                    self.active_fvgs.remove(fvg)
                    continue
                fvg["ce"] = fvg["bottom"] + (fvg["top"] - fvg["bottom"]) / 2
            else:
                if latest["high"] > fvg["bottom"]:
                    fvg["bottom"] = latest["high"]
                if fvg["bottom"] >= fvg["top"]:
                    self.active_fvgs.remove(fvg)
                    continue
                fvg["ce"] = fvg["top"] - (fvg["top"] - fvg["bottom"]) / 2

        # 2. Detect new FVG from the last 3 candles
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        
        # Bullish FVG
        if c3["low"] > c1["high"]:
            gap = c3["low"] - c1["high"]
            self.active_fvgs.append({
                "type": "BULLISH",
                "top": c3["low"],
                "bottom": c1["high"],
                "ce": c1["high"] + gap / 2,
                "index": c2.name
            })
            
        # Bearish FVG
        elif c1["low"] > c3["high"]:
            gap = c1["low"] - c3["high"]
            self.active_fvgs.append({
                "type": "BEARISH",
                "top": c1["low"],
                "bottom": c3["high"],
                "ce": c3["high"] + gap / 2,
                "index": c2.name
            })
        return self.active_fvgs
