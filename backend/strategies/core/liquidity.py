"""
backend/strategies/core/liquidity.py

Liquidity mapping: BSL, SSL, Equal Highs/Lows, Inducement (IDM), and Sweep detection.
Source: SMC_Strategy.md Section 4
"""

from typing import Any

import pandas as pd
from backend.utils.logger import get_logger

logger = get_logger(__name__)

class LiquidityMapper:
    """Maps liquidity pools, inducement (IDM), and detects sweeps."""

    def __init__(self, liq_sweep_min_atr_mult: float = 0.1, eq_tolerance_pips: float = 10.0, sweep_min_pips: float = None):
        if sweep_min_pips is not None:
            logger.warning("sweep_min_pips is deprecated; use liq_sweep_min_atr_mult instead.")
            self.atr_multiplier = sweep_min_pips
        else:
            self.atr_multiplier = liq_sweep_min_atr_mult
        self.eq_tolerance_pips = eq_tolerance_pips
        self.bsl_pools = []
        self.ssl_pools = []

    def update(self, candles: pd.DataFrame, swings: list[dict[str, Any]]) -> dict[str, Any]:
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
                logger.debug(f"New BSL pool added at {h['price']}")
        
        for l in lows:
            if l["price"] not in current_ssl_levels:
                self.ssl_pools.append({
                    "level": l["price"], 
                    "swept": False, 
                    "time": l.get("time")
                })
                logger.debug(f"New SSL pool added at {l['price']}")
                
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
        MAX_POOLS = 50
        if len(self.bsl_pools) > MAX_POOLS:
            dropped = len(self.bsl_pools) - MAX_POOLS
            self.bsl_pools = self.bsl_pools[-MAX_POOLS:]
            logger.debug(f"Pruned {dropped} old BSL pools due to size cap")
        if len(self.ssl_pools) > MAX_POOLS:
            dropped = len(self.ssl_pools) - MAX_POOLS
            self.ssl_pools = self.ssl_pools[-MAX_POOLS:]
            logger.debug(f"Pruned {dropped} old SSL pools due to size cap")
            
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
            
            if tr_list:
                atr = sum(tr_list) / len(tr_list)
            else:
                atr = latest["high"] - latest["low"]
            if atr <= 0:
                atr = 0.0001
            min_sweep_depth = self.atr_multiplier * atr
            
            # Check BSL sweeps (wick above pool by at least min_sweep_depth, close below)
            for pool in self.bsl_pools:
                if not pool["swept"]:
                    logger.debug(f"[TRACE] BSL Sweep Check | price: {latest['high']:.5f} | pool: {pool['level']:.5f} | required: {pool['level'] + min_sweep_depth:.5f}")
                    if latest["high"] >= pool["level"] + min_sweep_depth and latest["close"] < pool["level"]:
                        recent_sweep = {"type": "BSL", "level": pool["level"]}
                        pool["swept"] = True
                        logger.info(f"BSL Sweep detected at {pool['level']} (wick depth {latest['high'] - pool['level']:.5f})")
                else:
                    if latest["high"] >= pool["level"] + min_sweep_depth and latest["close"] < pool["level"]:
                        logger.debug(f"BSL Sweep condition met at {pool['level']}, but pool was already marked swept. Skipping.")
                        
            # Check SSL sweeps (wick below pool by at least min_sweep_depth, close above)
            for pool in self.ssl_pools:
                if not pool["swept"]:
                    logger.debug(f"[TRACE] SSL Sweep Check | price: {latest['low']:.5f} | pool: {pool['level']:.5f} | required: {pool['level'] - min_sweep_depth:.5f}")
                    if latest["low"] <= pool["level"] - min_sweep_depth and latest["close"] > pool["level"]:
                        recent_sweep = {"type": "SSL", "level": pool["level"]}
                        pool["swept"] = True
                        logger.info(f"SSL Sweep detected at {pool['level']} (wick depth {pool['level'] - latest['low']:.5f})")
                else:
                    if latest["low"] <= pool["level"] - min_sweep_depth and latest["close"] > pool["level"]:
                        logger.debug(f"SSL Sweep condition met at {pool['level']}, but pool was already marked swept. Skipping.")
                        
        return {
            "bsl": self.bsl_pools,
            "ssl": self.ssl_pools,
            "idm": idm_levels,
            "recent_sweep": recent_sweep
        }
