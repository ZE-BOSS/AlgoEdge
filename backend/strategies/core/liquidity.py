"""
backend/strategies/core/liquidity.py

Liquidity mapping: BSL, SSL, Equal Highs/Lows, Inducement (IDM), and Sweep detection.
Source: SMC_Strategy.md Section 4
"""

import pandas as pd
from typing import List, Dict, Any

class LiquidityMapper:
    """Maps liquidity pools, inducement (IDM), and detects sweeps."""

    def __init__(self, sweep_min_pips: float = 0.1, eq_tolerance_pips: float = 10.0):
        # sweep_min_pips is repurposed as atr_multiplier for dynamic threshold
        self.atr_multiplier = sweep_min_pips
        self.eq_tolerance_pips = eq_tolerance_pips
        self.bsl_pools = []
        self.ssl_pools = []

    def update(self, candles: pd.DataFrame, swings: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Identify new liquidity pools from swings, detect Inducement (IDM), and check for recent sweeps.
        """
        recent_sweep = None
        idm_levels = []
        
        # Build map of BSL/SSL from recent swings
        highs = [s for s in swings if s["type"] == "HIGH"][-5:]
        lows = [s for s in swings if s["type"] == "LOW"][-5:]
        
        current_bsl_levels = set(pool["level"] for pool in self.bsl_pools)
        current_ssl_levels = set(pool["level"] for pool in self.ssl_pools)
        
        # We assume recent highs are BSL and lows are SSL
        for h in highs:
            if h["price"] not in current_bsl_levels:
                self.bsl_pools.append({
                    "level": h["price"], 
                    "swept": False, 
                    "time": h.get("time")
                })
        
        for l in lows:
            if l["price"] not in current_ssl_levels:
                self.ssl_pools.append({
                    "level": l["price"], 
                    "swept": False, 
                    "time": l.get("time")
                })
                
        # IDM Detection (Inducement)
        # IDM is a local high/low forming between 20% and 70% retracement of the last impulse leg
        if len(swings) >= 2:
            last_swing = swings[-1]
            prev_swing = swings[-2]
            impulse_range = abs(last_swing["price"] - prev_swing["price"])
            
            if impulse_range > 0:
                # If impulse was BULLISH (prev was LOW, last is HIGH)
                if last_swing["type"] == "HIGH":
                    # Look for minor lows forming as IDM before the true POI (which is near prev_swing)
                    min_retrace_price = last_swing["price"] - (impulse_range * 0.2)
                    max_retrace_price = last_swing["price"] - (impulse_range * 0.7)
                    
                    # Any recent low (internal) between these prices is IDM
                    for pool in self.ssl_pools:
                        if max_retrace_price <= pool["level"] <= min_retrace_price:
                            idm_levels.append({"type": "IDM_LOW", "level": pool["level"]})
                            
                # If impulse was BEARISH (prev was HIGH, last is LOW)
                elif last_swing["type"] == "LOW":
                    # Look for minor highs forming as IDM
                    min_retrace_price = last_swing["price"] + (impulse_range * 0.2)
                    max_retrace_price = last_swing["price"] + (impulse_range * 0.7)
                    
                    for pool in self.bsl_pools:
                        if min_retrace_price <= pool["level"] <= max_retrace_price:
                            idm_levels.append({"type": "IDM_HIGH", "level": pool["level"]})

        # Prune old pools to prevent memory bloat
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
            "idm": idm_levels,
            "recent_sweep": recent_sweep
        }
