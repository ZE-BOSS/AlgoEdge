"""
backend/strategies/smc/liquidity.py

Liquidity mapping: BSL, SSL, Equal Highs/Lows, and Sweep detection.
Source: SMC_Strategy.md Section 3 & 4
"""

import pandas as pd
from typing import List, Dict, Any

class LiquidityMapper:
    """Maps liquidity pools and detects sweeps."""

    def __init__(self, sweep_min_pips: float = 5.0, eq_tolerance_pips: float = 10.0):
        self.sweep_min_pips = sweep_min_pips
        self.eq_tolerance_pips = eq_tolerance_pips
        self.bsl_pools = []
        self.ssl_pools = []

    def update(self, candles: pd.DataFrame, swings: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Identify new liquidity pools from swings and check for recent sweeps.
        """
        recent_sweep = None
        
        # Build map of BSL/SSL from swings
        highs = [s["price"] for s in swings if s["type"] == "HIGH"]
        lows = [s["price"] for s in swings if s["type"] == "LOW"]
        
        self.bsl_pools = []
        self.ssl_pools = []
        
        # For simplicity, store the most prominent pools
        if highs:
            self.bsl_pools.append({"level": max(highs), "swept": False})
        if lows:
            self.ssl_pools.append({"level": min(lows), "swept": False})
            
        if len(candles) > 0:
            latest = candles.iloc[-1]
            
            # Check BSL sweeps (wick above pool, close below)
            for pool in self.bsl_pools:
                if not pool["swept"]:
                    if latest["high"] > pool["level"] and latest["close"] < pool["level"]:
                        recent_sweep = {"type": "BSL", "level": pool["level"]}
                        pool["swept"] = True
                        
            # Check SSL sweeps (wick below pool, close above)
            for pool in self.ssl_pools:
                if not pool["swept"]:
                    if latest["low"] < pool["level"] and latest["close"] > pool["level"]:
                        recent_sweep = {"type": "SSL", "level": pool["level"]}
                        pool["swept"] = True
        return {
            "bsl": self.bsl_pools,
            "ssl": self.ssl_pools,
            "recent_sweep": recent_sweep
        }
