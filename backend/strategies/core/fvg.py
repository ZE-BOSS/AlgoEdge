"""
backend/strategies/smc/fvg.py

Fair Value Gap (FVG) detection and Consequent Encroachment (CE) levels.
Source: SMC_Strategy.md Section 2
"""

from typing import Any

import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)

class FVGDetector:
    """Detects 3-candle Fair Value Gaps."""

    def __init__(
        self,
        fvg_min_gap_atr_mult: float = 0.2,
        min_gap_pips: float = None,
        max_fvgs: int = 20,
        displacement_atr_mult: float = 0.0,
        displacement_body_pct: float = 0.0,
    ):
        # Backward compatibility for one release
        if min_gap_pips is not None:
            logger.warning("min_gap_pips is deprecated; use fvg_min_gap_atr_mult instead.")
            self.atr_multiplier = min_gap_pips
        else:
            self.atr_multiplier = fvg_min_gap_atr_mult
        self.active_fvgs = []
        self.max_fvgs = max_fvgs
        # [6.11/S13/G8] Displacement gate: admit an FVG only when the MIDDLE
        # candle (the one whose directional move actually created the gap) is
        # itself a genuine displacement candle — large range and a dominant
        # body, not just any 3-candle sequence whose gap happens to clear the
        # ATR-scaled size threshold above. 0 = disabled (the default, so every
        # existing caller — e.g. Bias-IFVG's M5 IFVG detector — is unaffected;
        # this is opt-in per instance, wired on for HTF FVG Flip's HTF-level
        # detector specifically).
        self.displacement_atr_mult = displacement_atr_mult or 0.0
        self.displacement_body_pct = displacement_body_pct or 0.0

    def update(self, candles: pd.DataFrame) -> list[dict[str, Any]]:
        """
        Scan for new FVGs and remove mitigated ones.
        """
        if len(candles) < 3:
            return self.active_fvgs

        # 1. Check for fills of existing FVGs
        latest = candles.iloc[-1]
        for fvg in self.active_fvgs[:]:
            if fvg["type"] == "BULLISH":
                fvg["top"] = min(fvg["top"], latest["low"])
                if fvg["top"] <= fvg["bottom"]:
                    self.active_fvgs.remove(fvg)
                    logger.debug(f"BULLISH FVG filled/removed at {latest.name}")
                    continue
                fvg["ce"] = fvg["bottom"] + (fvg["top"] - fvg["bottom"]) / 2
            else:
                fvg["bottom"] = max(fvg["bottom"], latest["high"])
                if fvg["bottom"] >= fvg["top"]:
                    self.active_fvgs.remove(fvg)
                    logger.debug(f"BEARISH FVG filled/removed at {latest.name}")
                    continue
                fvg["ce"] = fvg["top"] - (fvg["top"] - fvg["bottom"]) / 2

        # 2. Calculate dynamic ATR for the gap constraint
        # We look back at most 14 candles to calculate the Average True Range
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
            logger.warning("tr_list is empty, falling back to current candle range for ATR")
            atr = candles.iloc[-1]["high"] - candles.iloc[-1]["low"]
            
        if atr <= 0:
            atr = 0.0001
            
        min_required_gap = self.atr_multiplier * atr

        # 3. Detect new FVG from the last 3 candles
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]

        # [6.11/S13/G8] Displacement gate on the MIDDLE candle (c2) — the one
        # whose directional move actually created the gap.
        displacement_ok = True
        if self.displacement_atr_mult > 0 or self.displacement_body_pct > 0:
            c2_range = c2["high"] - c2["low"]
            c2_body = abs(c2["close"] - c2["open"])
            if self.displacement_atr_mult > 0 and atr > 0:
                displacement_ok = displacement_ok and (c2_range >= self.displacement_atr_mult * atr)
            if self.displacement_body_pct > 0:
                displacement_ok = displacement_ok and (c2_range > 0 and (c2_body / c2_range) >= self.displacement_body_pct)

        # Bullish FVG
        if c3["low"] > c1["high"]:
            gap = c3["low"] - c1["high"]
            logger.debug(f"[TRACE] FVG Check | ATR: {atr:.5f} | min_gap: {min_required_gap:.5f} | actual gap: {gap:.5f} | PASS: {gap >= min_required_gap and displacement_ok}")
            if gap >= min_required_gap and displacement_ok:
                new_fvg = {
                    "type": "BULLISH",
                    "top": c3["low"],
                    "bottom": c1["high"],
                    "ce": c1["high"] + gap / 2,
                    "index": c2.name
                }
                self.active_fvgs.append(new_fvg)
                logger.debug(f"New BULLISH FVG created: {new_fvg}")
            
        # Bearish FVG
        elif c1["low"] > c3["high"]:
            gap = c1["low"] - c3["high"]
            logger.debug(f"[TRACE] FVG Check | ATR: {atr:.5f} | min_gap: {min_required_gap:.5f} | actual gap: {gap:.5f} | PASS: {gap >= min_required_gap and displacement_ok}")
            if gap >= min_required_gap and displacement_ok:
                new_fvg = {
                    "type": "BEARISH",
                    "top": c1["low"],
                    "bottom": c3["high"],
                    "ce": c3["high"] + gap / 2,
                    "index": c2.name
                }
                self.active_fvgs.append(new_fvg)
                logger.debug(f"New BEARISH FVG created: {new_fvg}")

        # 4. Prune array to prevent memory leaks in backtesting
        if len(self.active_fvgs) > self.max_fvgs:
            self.active_fvgs = self.active_fvgs[-self.max_fvgs:]

        return self.active_fvgs
