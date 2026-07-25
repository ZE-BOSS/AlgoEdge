"""
backend/strategies/smc/supply_demand.py

Supply and Demand zone detection (RBD, DBR).
Provides broader context for Order Blocks.
"""

from typing import Any

import pandas as pd


class SupplyDemandDetector:
    """Detects Drop-Base-Rally and Rally-Base-Drop zones."""

    def __init__(self, max_age: int = 20):
        self.supply_zones = []
        self.demand_zones = []
        self.max_age = max_age

    def update(self, candles: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
        """Update active supply and demand zones."""
        if len(candles) < 4:
            return {"supply": self.supply_zones, "demand": self.demand_zones}
            
        latest_close = candles.iloc[-1]["close"]
        current_len = len(candles)
        
        # Invalidate mitigated or expired zones
        valid_demand = []
        for z in self.demand_zones:
            if latest_close < z["bottom"]:
                continue # Mitigated
            if current_len - z.get("created_len", current_len) > self.max_age:
                continue # Expired
            valid_demand.append(z)
        self.demand_zones = valid_demand
        
        valid_supply = []
        for z in self.supply_zones:
            if latest_close > z["top"]:
                continue # Mitigated
            if current_len - z.get("created_len", current_len) > self.max_age:
                continue # Expired
            valid_supply.append(z)
        self.supply_zones = valid_supply
            
        # Analyze last 4 candles to find patterns
        # c0: Drop/Rally, c1: Base, c2: Drop/Rally
        c0, c1, c2 = candles.iloc[-4], candles.iloc[-3], candles.iloc[-2]
        
        c0_body = c0["close"] - c0["open"]
        c1_body = c1["close"] - c1["open"]
        c2_body = c2["close"] - c2["open"]
        
        c1_range = c1["high"] - c1["low"]
        if c1_range == 0:
            c1_range = 0.0001
            
        is_c1_base = abs(c1_body) < (c1_range * 0.5)
        
        # DBR (Demand Zone)
        if c0_body < 0 and is_c1_base and c2_body > 0 and abs(c2_body) > abs(c0_body) * 0.5:
            if not any(z["index"] == c1.name for z in self.demand_zones):
                self.demand_zones.append({
                    "top": c1["high"],
                    "bottom": c1["low"],
                    "index": c1.name,
                    "created_len": current_len
                })
            
        # RBD (Supply Zone)
        if c0_body > 0 and is_c1_base and c2_body < 0 and abs(c2_body) > abs(c0_body) * 0.5:
            if not any(z["index"] == c1.name for z in self.supply_zones):
                self.supply_zones.append({
                    "top": c1["high"],
                    "bottom": c1["low"],
                    "index": c1.name,
                    "created_len": current_len
                })
                
        return {
            "supply": self.supply_zones,
            "demand": self.demand_zones
        }
