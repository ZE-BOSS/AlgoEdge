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
        score = 0

        direction = context.get("signal_direction", "")

        # +15: HTF (H4) bias confirmed
        htf_bias = context.get("htf_bias", "NEUTRAL")
        if htf_bias == direction:
            score += 15

        # +10: H1 structure aligns with signal direction
        h1_struct = context.get("h1_structure", "NEUTRAL")
        if h1_struct == direction:
            score += 10

        # +15: Liquidity sweep detected
        sweep = context.get("liquidity_sweep")
        if sweep is not None:
            # BSL sweep = bearish signal, SSL sweep = bullish signal
            if (sweep.get("type") == "SSL" and direction == "BULLISH") or \
               (sweep.get("type") == "BSL" and direction == "BEARISH"):
                score += 15

        # +15: Fresh Order Block (first touch, unmitigated)
        ob = context.get("fresh_ob")
        if ob is not None and ob.get("touches", 99) == 0:
            if ob.get("type") == direction:
                score += 15

        # +10: FVG inside OB (highest probability confluence)
        if context.get("fvg_inside_ob", False):
            score += 10

        # +5: In OTE zone (Fibonacci 61.8%-78.6% retracement)
        if context.get("in_ote_zone", False):
            score += 5

        # +15/+10/+5: Candlestick confirmation tier
        candle_tier = context.get("candle_tier", 0)
        if candle_tier == 1:
            score += 15  # Tier 1: Engulfing, Hammer, Shooting Star
        elif candle_tier == 2:
            score += 10  # Tier 2: Doji, Morning/Evening Star
        elif candle_tier == 3:
            score += 5   # Tier 3: Inside Bar, Rejection Wick

        # +10: LTF ChoCH confirmed (M5 change of character)
        if context.get("ltf_choch", False):
            score += 10

        # +5: Active kill zone session
        if context.get("in_kill_zone", False):
            score += 5

        return min(score, 100)
