"""
backend/strategies/smc/fvg.py

Fair Value Gap (FVG) detection and Consequent Encroachment (CE) levels.
Source: SMC_Strategy.md Section 2
"""

from typing import Any

import numpy as np
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

        PERFORMANCE [L1-opt]
        --------------------
        This was the single hottest function in the whole backtester. Profiling
        BiasIFVG over 4,000 M5 bars: `FVGDetector.update` accounted for **129 s
        of 148 s total (87%)**, and inside it `DataFrame._ixs -> fast_xs` was
        81 s on its own.

        The cause was row-wise pandas access. Every call did:
          * `candles.iloc[-1]`, `.iloc[-3]`, `.iloc[-2]` — four Series
            constructions per bar, each running `fast_xs` + `find_common_type`
            over the frame's blocks;
          * an ATR loop doing `recent.iloc[i]` and `recent.iloc[i-1]` for 14
            iterations — **28 more Series constructions per bar**.

        That is ~32 Series objects built per bar purely to read six floats.

        Now: pull the four OHLC columns to numpy once (`to_numpy(copy=False)` is
        a view, not a copy) and index them positionally. Identical arithmetic,
        identical outputs — the only thing removed is pandas' per-row object
        overhead.
        """
        n = len(candles)
        if n < 3:
            return self.active_fvgs

        # Views, not copies. The frame is a slice the caller already owns.
        try:
            highs = candles["high"].to_numpy(dtype=float, copy=False)
            lows = candles["low"].to_numpy(dtype=float, copy=False)
            closes = candles["close"].to_numpy(dtype=float, copy=False)
            opens = candles["open"].to_numpy(dtype=float, copy=False)
        except (KeyError, ValueError):
            # Malformed frame — behave as "no data" rather than raising into
            # the strategy's bar loop.
            return self.active_fvgs

        last_high = highs[-1]
        last_low = lows[-1]

        # 1. Check for fills of existing FVGs
        for fvg in self.active_fvgs[:]:
            if fvg["type"] == "BULLISH":
                if last_low < fvg["top"]:
                    fvg["top"] = last_low
                if fvg["top"] <= fvg["bottom"]:
                    self.active_fvgs.remove(fvg)
                    continue
                fvg["ce"] = fvg["bottom"] + (fvg["top"] - fvg["bottom"]) / 2
            else:
                if last_high > fvg["bottom"]:
                    fvg["bottom"] = last_high
                if fvg["bottom"] >= fvg["top"]:
                    self.active_fvgs.remove(fvg)
                    continue
                fvg["ce"] = fvg["top"] - (fvg["top"] - fvg["bottom"]) / 2

        # 2. ATR over the last <=14 bars — vectorised true range.
        lookback = min(14, n - 1)
        if lookback > 0:
            h = highs[-lookback:]
            lo = lows[-lookback:]
            pc = closes[-(lookback + 1):-1]          # previous close, aligned
            tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
            atr = float(tr.mean())
        else:
            atr = float(highs[-1] - lows[-1])

        if atr <= 0:
            atr = 0.0001

        min_required_gap = self.atr_multiplier * atr

        # 3. Detect a new FVG from the last 3 candles
        c1_high, c1_low = highs[-3], lows[-3]
        c2_high, c2_low = highs[-2], lows[-2]
        c2_close, c2_open = closes[-2], opens[-2]
        c3_high, c3_low = highs[-1], lows[-1]

        # Displacement gate on the MIDDLE candle (c2) — the one whose
        # directional move actually created the gap.
        displacement_ok = True
        if self.displacement_atr_mult > 0 or self.displacement_body_pct > 0:
            c2_range = c2_high - c2_low
            c2_body = abs(c2_close - c2_open)
            if self.displacement_atr_mult > 0 and atr > 0:
                displacement_ok = c2_range >= self.displacement_atr_mult * atr
            if displacement_ok and self.displacement_body_pct > 0:
                displacement_ok = c2_range > 0 and (c2_body / c2_range) >= self.displacement_body_pct

        # `index` must stay the middle candle's label, which callers use to age
        # and draw the zone — resolved only when an FVG is actually created,
        # rather than on every bar.
        if c3_low > c1_high:
            gap = c3_low - c1_high
            if gap >= min_required_gap and displacement_ok:
                self.active_fvgs.append({
                    "type": "BULLISH",
                    "top": float(c3_low),
                    "bottom": float(c1_high),
                    "ce": float(c1_high + gap / 2),
                    "index": candles.index[-2],
                })
        elif c1_low > c3_high:
            gap = c1_low - c3_high
            if gap >= min_required_gap and displacement_ok:
                self.active_fvgs.append({
                    "type": "BEARISH",
                    "top": float(c1_low),
                    "bottom": float(c3_high),
                    "ce": float(c3_high + gap / 2),
                    "index": candles.index[-2],
                })

        # 4. Prune array to prevent memory leaks in backtesting
        if len(self.active_fvgs) > self.max_fvgs:
            self.active_fvgs = self.active_fvgs[-self.max_fvgs:]

        return self.active_fvgs
