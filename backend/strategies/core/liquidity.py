"""
backend/strategies/smc/liquidity.py

Liquidity mapping: BSL, SSL, Equal Highs/Lows, and Sweep detection.
Source: SMC_Strategy.md Section 3 & 4
"""

import pandas as pd
from typing import List, Dict, Any

class LiquidityMapper:
    """Maps liquidity pools and detects sweeps."""

    def __init__(self, sweep_min_pips: float = 0.1, eq_tolerance_pips: float = 10.0):
        # sweep_min_pips is repurposed as atr_multiplier
        self.atr_multiplier = sweep_min_pips
        self.eq_tolerance_pips = eq_tolerance_pips
        self.bsl_pools = []
        self.ssl_pools = []

    def update(self, candles: pd.DataFrame, swings: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Identify new liquidity pools from swings and check for recent sweeps.
        """
        recent_sweep = None
        
        # Build map of BSL/SSL from recent swings (e.g., last 3-5 swings)
        highs = [s["price"] for s in swings if s["type"] == "HIGH"][-5:]
        lows = [s["price"] for s in swings if s["type"] == "LOW"][-5:]
        
        # Initialize pools if not present, retaining 'swept' state
        current_bsl_levels = set(pool["level"] for pool in self.bsl_pools)
        current_ssl_levels = set(pool["level"] for pool in self.ssl_pools)
        
        for h in highs:
            if h not in current_bsl_levels:
                self.bsl_pools.append({"level": h, "swept": False})
        
        for l in lows:
            if l not in current_ssl_levels:
                self.ssl_pools.append({"level": l, "swept": False})
                
        # Optional: Prune old pools to prevent memory bloat
        if len(self.bsl_pools) > 10:
            self.bsl_pools = self.bsl_pools[-10:]
        if len(self.ssl_pools) > 10:
            self.ssl_pools = self.ssl_pools[-10:]
            
        if len(candles) > 0:
            latest = candles.iloc[-1]
            
            # Calculate dynamic ATR for sweep depth
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
            
            atr = sum(tr_list) / len(tr_list) if tr_list else (latest["high"] - latest["low"])
            min_sweep_depth = self.atr_multiplier * atr
            
            # Check BSL sweeps (wick above pool by at least min_sweep_depth, close below)
            for pool in self.bsl_pools:
                if not pool["swept"]:
                    if latest["high"] >= pool["level"] + min_sweep_depth and latest["close"] < pool["level"]:
                        recent_sweep = {"type": "BSL", "level": pool["level"]}
                        pool["swept"] = True
                        
            # Check SSL sweeps (wick below pool by at least min_sweep_depth, close above)
            for pool in self.ssl_pools:
                if not pool["swept"]:
                    if latest["low"] <= pool["level"] - min_sweep_depth and latest["close"] > pool["level"]:
                        recent_sweep = {"type": "SSL", "level": pool["level"]}
                        pool["swept"] = True
                        
        return {
            "bsl": self.bsl_pools,
            "ssl": self.ssl_pools,
            "recent_sweep": recent_sweep
        }
