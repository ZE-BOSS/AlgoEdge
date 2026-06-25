"""
backend/strategies/smc/confluence.py

Confluence scoring system (0-100) for SMC signals.
Source: TradingBot_MasterPlan-2.md Section 4 — Confluence Scoring Gate
Source: SMC_Strategy-1.md Section 13
"""

from typing import Dict, Any
from backend.strategies.smc.params import SMCParams


class ConfluenceScorer:
    """Calculates trade signal strength based on multiple SMC factors."""

    def __init__(self, params: SMCParams):
        self.params = params

    def calculate_score(self, context: Dict[str, Any]) -> int:
        """
        Evaluate context dictionary and return score 0-100 (capped at 100).

        Expected context keys:
            htf_bias:          str  ("BULLISH"/"BEARISH")
            h1_structure:      str  ("BULLISH"/"BEARISH"/"NEUTRAL")
            signal_direction:  str  ("BULLISH"/"BEARISH")
            liquidity_sweep:   dict or None  ({"type":"BSL"/"SSL", "level":...})
            fresh_ob:          dict or None  (with "type", "touches")
            fvg_inside_ob:     bool
            in_ote_zone:       bool
            candle_tier:       int  (1, 2, or 3)
            ltf_choch:         bool
            in_kill_zone:      bool
        """
        # Base score for passing structural gates (H4 Bias + H1 BOS + IPDM + M15 ChoCH)
        # We only reach the scorer if these gates have already been passed.
        score = 40
        breakdown = {"base_structure": 40}

        # ENTRY ZONE CONFIRMATION (Max +20)
        zone_score = 0
        zone_name = "None"
        if context.get("fresh_ob") is not None:
            zone_score = 20
            zone_name = "Fresh OB"
        elif context.get("fvg_inside_ob", False):
            zone_score = 20
            zone_name = "FVG inside OB"
        elif context.get("in_ote_zone", False):
            zone_score = 15
            zone_name = "OTE Fib Zone"
        elif context.get("in_sd_zone", False):
            zone_score = 15
            zone_name = "S&D Zone"
        
        score += zone_score
        breakdown[f"entry_zone ({zone_name})"] = zone_score

        # LIQUIDITY SWEEP (Max +10)
        sweep_score = 0
        sweep = context.get("liquidity_sweep")
        if sweep is not None:
            if (sweep.get("type") == "SSL" and context.get("signal_direction") == "BULLISH") or \
               (sweep.get("type") == "BSL" and context.get("signal_direction") == "BEARISH"):
                sweep_score = 10
                
        score += sweep_score
        breakdown["liquidity_sweep"] = sweep_score

        # CANDLESTICK CONFIRMATION (Max +20)
        candle_score = 0
        candle_tier = context.get("candle_tier", 0)
        if candle_tier == 1:
            candle_score = 20
        elif candle_tier == 2:
            candle_score = 15
        elif candle_tier == 3:
            candle_score = 10
            
        score += candle_score
        breakdown[f"candlestick (tier {candle_tier})"] = candle_score

        # KILL ZONE / SESSION ALIGNMENT (Max +10)
        kz_score = 0
        if context.get("in_kill_zone", False):
            kz_score = 10
            
        score += kz_score
        breakdown["kill_zone"] = kz_score
        
        # Inject the breakdown directly into context so it can be logged
        context["score_breakdown"] = breakdown

        return min(score, 100)
