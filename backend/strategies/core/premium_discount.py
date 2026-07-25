"""
backend/strategies/smc/premium_discount.py

Fibonacci Premium, Discount, and Optimal Trade Entry (OTE) zones.
Source: SMC_Strategy.md Section 3
"""


class PremiumDiscountCalculator:
    """Calculates Fibonacci retracement levels for a given price leg."""

    def __init__(self, ote_min: float = 0.618, ote_max: float = 0.786):
        self.ote_min = ote_min
        self.ote_max = ote_max

    def calculate(self, swing_high: float, swing_low: float) -> dict[str, float]:
        """
        Calculate Premium/Discount zones and OTE levels.
        """
        range_size = swing_high - swing_low
        if range_size == 0:
            return {}
            
        return {
            "equilibrium": swing_high - (range_size * 0.5),
            "ote_long_top": swing_high - (range_size * self.ote_min),
            "ote_long_bottom": swing_high - (range_size * self.ote_max),
            "ote_short_bottom": swing_low + (range_size * self.ote_min),
            "ote_short_top": swing_low + (range_size * self.ote_max),
        }
