"""
backend/strategies/smc/order_blocks.py

Order Block (OB) identification and mitigation tracking.
Source: SMC_Strategy.md Section 2
"""

import pandas as pd
from typing import List, Dict, Any

class OrderBlockDetector:
    """Identifies fresh order blocks and tracks their mitigation status."""

    def __init__(self, impulse_ratio: float = 2.0, max_touches: int = 1):
        self.impulse_ratio = impulse_ratio
        self.max_touches = max_touches
        self.active_obs = []

    def update(self, candles: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Scan for new OBs and update mitigation status of existing ones.
        """
        if len(candles) < 3:
            return self.active_obs

        # 1. Update mitigation of existing OBs
        latest_candle = candles.iloc[-1]
        for ob in self.active_obs:
            if ob["mitigated"]: continue
            
            if ob["type"] == "BULLISH":
                # Touch: price drops into bullish OB
                if latest_candle["low"] <= ob["top"]:
                    ob["touches"] += 1
                # Mitigation: price closes below bullish OB bottom
                if latest_candle["close"] < ob["bottom"]:
                    ob["mitigated"] = True
            else:
                # Touch: price rises into bearish OB
                if latest_candle["high"] >= ob["bottom"]:
                    ob["touches"] += 1
                # Mitigation: price closes above bearish OB top
                if latest_candle["close"] > ob["top"]:
                    ob["mitigated"] = True

        self.active_obs = [ob for ob in self.active_obs if not ob["mitigated"]]

        # 2. Scan for new OBs (simplified logic for last 3 candles)
        c1 = candles.iloc[-3] # The base candle
        c2 = candles.iloc[-2] # The impulse candle
        
        c1_body = abs(c1["close"] - c1["open"])
        c2_body = abs(c2["close"] - c2["open"])
        
        if c1_body == 0:
            c1_body = 0.0001
            
        is_impulse = (c2_body / c1_body) >= self.impulse_ratio
        
        if is_impulse:
            if c2["close"] > c2["open"] and c1["close"] < c1["open"]:
                # Bullish impulse after bearish candle -> Bullish OB
                self.active_obs.append({
                    "type": "BULLISH",
                    "top": c1["high"],
                    "bottom": c1["low"],
                    "index": c1.name,
                    "mitigated": False,
                    "touches": 0
                })
            elif c2["close"] < c2["open"] and c1["close"] > c1["open"]:
                # Bearish impulse after bullish candle -> Bearish OB
                self.active_obs.append({
                    "type": "BEARISH",
                    "top": c1["high"],
                    "bottom": c1["low"],
                    "index": c1.name,
                    "mitigated": False,
                    "touches": 0
                })
        return self.active_obs
