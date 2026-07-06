"""
backend/strategies/smc/fvg.py

Fair Value Gap (FVG) detection and Consequent Encroachment (CE) levels.
Source: SMC_Strategy.md Section 2
"""

import pandas as pd
from typing import List, Dict, Any

class FVGDetector:
    """Detects 3-candle Fair Value Gaps."""

    def __init__(self, min_gap_pips: float = 0.2):
        # We repurposed min_gap_pips to act as an ATR multiplier.
        # e.g., 0.2 means the gap must be at least 0.2 * ATR.
        self.atr_multiplier = min_gap_pips
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

        # 2. Calculate dynamic ATR for the gap constraint
        # We look back at most 14 candles to calculate the Average True Range
        lookback = min(14, len(candles) - 1)
        recent_candles = candles.iloc[-(lookback+1):]
        
        tr_list = []
        for i in range(1, len(recent_candles)):
            c = recent_candles.iloc[i]
            prev_c = recent_candles.iloc[i-1]
            tr = max(
                c["high"] - c["low"],
                abs(c["high"] - prev_c["close"]),
                abs(c["low"] - prev_c["close"])
            )
            tr_list.append(tr)
        
        atr = sum(tr_list) / len(tr_list) if tr_list else (candles.iloc[-1]["high"] - candles.iloc[-1]["low"])
        min_required_gap = self.atr_multiplier * atr

        # 3. Detect new FVG from the last 3 candles
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        
        # Bullish FVG
        if c3["low"] > c1["high"]:
            gap = c3["low"] - c1["high"]
            if gap >= min_required_gap:
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
            if gap >= min_required_gap:
                self.active_fvgs.append({
                    "type": "BEARISH",
                    "top": c1["low"],
                    "bottom": c3["high"],
                    "ce": c3["high"] + gap / 2,
                    "index": c2.name
                })
        return self.active_fvgs
