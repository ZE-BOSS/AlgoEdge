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
        # We only reach the scorer if base gates have passed.
        # We assign the base structural points automatically.
        score = 0
        breakdown = {}

        # 1. HTF Bias (Max 15)
        bias_score = 15 if context.get("htf_bias") == context.get("signal_direction") else 0
        score += bias_score
        breakdown["htf_bias"] = bias_score

        # 2. H1 Structure (Max 10)
        h1_score = 10 if context.get("h1_structure") == context.get("signal_direction") else 0
        score += h1_score
        breakdown["h1_structure"] = h1_score

        # 3. LTF ChoCH (Max 10)
        choch_score = 10 if context.get("ltf_choch", False) else 0
        score += choch_score
        breakdown["ltf_choch"] = choch_score

        # 4. Liquidity Sweep (Max 15)
        sweep_score = 0
        sweep = context.get("liquidity_sweep")
        if sweep is not None:
            if (sweep.get("type") == "SSL" and context.get("signal_direction") == "BULLISH") or \
               (sweep.get("type") == "BSL" and context.get("signal_direction") == "BEARISH"):
                sweep_score = 15
        score += sweep_score
        breakdown["sweep"] = sweep_score

        # 5. Fresh OB (Max 15)
        ob_score = 0
        if context.get("fresh_ob") is not None:
            ob_score = 15
        elif context.get("in_sd_zone", False):
            ob_score = 10 # Fallback for S&D
        score += ob_score
        breakdown["fresh_ob"] = ob_score

        # 6. FVG Inside OB / Present (Max 10)
        fvg_score = 0
        if context.get("fvg_inside_ob", False):
            fvg_score = 10
        elif context.get("fvg_present", False):
            fvg_score = 5
        score += fvg_score
        breakdown["fvg_inside_ob"] = fvg_score

        # 7. OTE Zone (Max 5)
        ote_score = 5 if context.get("in_ote_zone", False) else 0
        score += ote_score
        breakdown["ote_zone"] = ote_score

        # 8. Candlestick Confirmation (Max 15)
        candle_score = 0
        candle_tier = context.get("candle_tier", 0)
        if candle_tier == 1:
            candle_score = 15
        elif candle_tier == 2:
            candle_score = 10
        elif candle_tier == 3:
            candle_score = 5
        score += candle_score
        breakdown["candle"] = candle_score

        # 9. Kill Zone (Max 5)
        kz_score = 5 if context.get("in_kill_zone", False) else 0
        score += kz_score
        breakdown["kill_zone"] = kz_score

        score = min(score, 100)
        context["score_breakdown"] = breakdown

        return min(score, 100)
