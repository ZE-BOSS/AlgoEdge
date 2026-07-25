"""
backend/strategies/smc/order_blocks.py

Order Block (OB) identification and mitigation tracking.
Source: SMC_Strategy.md Section 2
"""

from typing import Any

import pandas as pd


class OrderBlockDetector:
    """Identifies fresh order blocks and tracks their mitigation status."""

    def __init__(self, impulse_ratio: float = 2.0, max_touches: int = 1):
        self.impulse_ratio = impulse_ratio
        self.max_touches = max_touches
        self.active_obs = []

    def update(self, candles: pd.DataFrame) -> list[dict[str, Any]]:
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
                if latest_candle["close"] < ob["bottom"] or ob["touches"] >= self.max_touches:
                    ob["mitigated"] = True
            else:
                # Touch: price rises into bearish OB
                if latest_candle["high"] >= ob["bottom"]:
                    ob["touches"] += 1
                # Mitigation: price closes above bearish OB top
                if latest_candle["close"] > ob["top"] or ob["touches"] >= self.max_touches:
                    ob["mitigated"] = True

        self.active_obs = [ob for ob in self.active_obs if not ob["mitigated"]]

        # 2. Scan for new OBs (multi-candle lookback for impulse)
        lookback = min(len(candles), 6)
        c_last = candles.iloc[-2] # Most recent closed candle
        
        for i in range(-lookback, -2):
            c_base = candles.iloc[i]
            c_base_body = abs(c_base["close"] - c_base["open"])
            
            if c_base_body == 0:
                c_base_body = 0.0001
                
            # Measure impulse from base candle close to last closed candle close
            impulse_size = abs(c_last["close"] - c_base["close"])
            is_impulse = (impulse_size / c_base_body) >= self.impulse_ratio
            
            if is_impulse:
                # Check if this OB is already tracked
                if any(ob["index"] == c_base.name for ob in self.active_obs):
                    continue
                    
                if c_last["close"] > c_base["close"] and c_base["close"] < c_base["open"]:
                    # Bullish impulse after bearish candle
                    self.active_obs.append({
                        "type": "BULLISH",
                        "top": c_base["high"],
                        "bottom": c_base["low"],
                        "index": c_base.name,
                        "mitigated": False,
                        "touches": 0
                    })
                elif c_last["close"] < c_base["close"] and c_base["close"] > c_base["open"]:
                    # Bearish impulse after bullish candle
                    self.active_obs.append({
                        "type": "BEARISH",
                        "top": c_base["high"],
                        "bottom": c_base["low"],
                        "index": c_base.name,
                        "mitigated": False,
                        "touches": 0
                    })
        
        return self.active_obs
