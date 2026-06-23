"""
backend/strategies/smc/supply_demand.py

Supply and Demand zone detection (RBD, DBR).
Provides broader context for Order Blocks.
"""

import pandas as pd
from typing import List, Dict, Any

class SupplyDemandDetector:
    """Detects Drop-Base-Rally and Rally-Base-Drop zones."""

    def __init__(self):
        self.supply_zones = []
        self.demand_zones = []

    def update(self, candles: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
        """Update active supply and demand zones."""
        if len(candles) < 4:
            return {"supply": self.supply_zones, "demand": self.demand_zones}
            
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
            self.demand_zones.append({
                "top": c1["high"],
                "bottom": c1["low"],
                "index": c1.name
            })
            
        # RBD (Supply Zone)
        if c0_body > 0 and is_c1_base and c2_body < 0 and abs(c2_body) > abs(c0_body) * 0.5:
            self.supply_zones.append({
                "top": c1["high"],
                "bottom": c1["low"],
                "index": c1.name
            })
        return {
            "supply": self.supply_zones,
            "demand": self.demand_zones
        }
