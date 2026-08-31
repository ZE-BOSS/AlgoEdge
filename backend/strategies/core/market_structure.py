"""
backend/strategies/smc/market_structure.py

Market structure detection: Swings, Break of Structure (BOS), Change of Character (ChoCH).
Source: SMC_Strategy-1.md Section 1 + Implementation Plan

Key rules:
  - BOS: price CLOSES above/below swing high/low (full body, not wick)
  - Trend confirmed only after min_bos_count (default 2) consecutive BOS in same direction
  - ChoCH: price body breaks past 2+ previous swing highs/lows in opposite direction
  - ChoCH uses candle CLOSE, not HIGH/LOW (full body confirmation)
"""

from typing import Any

import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)


class MarketStructureDetector:
    """Detects HH, HL, LH, LL and structural breaks with BOS counting."""

    def __init__(self, swing_length: int = 5, min_bos_count: int = 2):
        self.swing_length = swing_length
        self.min_bos_count = min_bos_count
        self.swings: list[dict[str, Any]] = []
        self.trend = "NEUTRAL"
        self.bos_history: list[dict[str, Any]] = []
        self.consecutive_bos = 0
        self.trend_confirmed = False
        self._last_bos_level: float | None = None  # Price of last BOS break
        self._last_bos_index: int | None = None  # bar_idx of the swing used for the last BOS/ChoCH break (dedup key)

    def update(self, candles: pd.DataFrame) -> dict[str, Any]:
        """
        Process candles to find swings and structural breaks.
        Returns trend info with BOS counter and confirmation state.
        """
        if len(candles) < self.swing_length * 2 + 1:
            return self._result(None, None)

        # Calculate local extremums for swings
        window = self.swing_length * 2 + 1
        highs = candles['high'].rolling(window=window, center=True).max()
        is_swing_high = candles['high'] == highs

        lows = candles['low'].rolling(window=window, center=True).min()
        is_swing_low = candles['low'] == lows

        self.swings = []
        
        # Vectorized extraction of swing points
        import numpy as np
        high_indices = np.where(is_swing_high)[0]
        low_indices = np.where(is_swing_low)[0]
        
        # Extract raw numpy arrays for nanosecond access times (bypassing pandas Series creation overhead)
        high_vals = candles['high'].values
        low_vals = candles['low'].values
        high_roll_vals = highs.values
        low_roll_vals = lows.values
        idx_vals = candles.index
        
        # Robust time extraction
        if 'time' in candles.columns:
            time_vals = candles['time'].values
        elif pd.api.types.is_datetime64_any_dtype(candles.index):
            time_vals = candles.index.astype('int64') // 10**9
        else:
            time_vals = [0] * len(candles)
            
        swings_list = []
        
        for i in high_indices:
            if not pd.isna(high_roll_vals[i]):
                swings_list.append({
                    "type": "HIGH",
                    "price": float(high_vals[i]),
                    "index": idx_vals[i],
                    "time": int(time_vals[i]),
                    "bar_idx": int(i),
                })
                
        for i in low_indices:
            if not pd.isna(low_roll_vals[i]):
                swings_list.append({
                    "type": "LOW",
                    "price": float(low_vals[i]),
                    "index": idx_vals[i],
                    "time": int(time_vals[i]),
                    "bar_idx": int(i),
                })
                
        # Sort sequentially by bar index
        self.swings = sorted(swings_list, key=lambda x: x["bar_idx"])

        last_bos = None
        last_choch = None

        high_swings = [s for s in self.swings if s["type"] == "HIGH"]
        low_swings = [s for s in self.swings if s["type"] == "LOW"]

        if len(high_swings) >= 2 and len(low_swings) >= 2:
            # Use candle CLOSE to check breaks (full body, not wick)
            latest_close = float(candles['close'].iloc[-1])

            prev_sh = high_swings[-2]["price"]
            curr_sh = high_swings[-1]["price"]
            prev_sl = low_swings[-2]["price"]
            curr_sl = low_swings[-1]["price"]

            hh = curr_sh > prev_sh
            hl = curr_sl > prev_sl
            lh = curr_sh < prev_sh
            ll = curr_sl < prev_sl

            # Logging TRACE
            logger.debug(
                f"[TRACE] MarketStructure | Trend: {self.trend} | BOS count: {self.consecutive_bos} | "
                f"Swings - SH: {prev_sh}->{curr_sh} (HH:{hh}, LH:{lh}) | "
                f"SL: {prev_sl}->{curr_sl} (HL:{hl}, LL:{ll})"
            )

            # Decoupled Break checks: A break happens when the price closes above the most
            # recent confirmed swing high (curr_sh) or below the most recent confirmed swing
            # low (curr_sl) — NOT the older prev_sh/prev_sl, which would compare against a
            # stale swing point one step behind the actual most-recent structure.
            # Check upside break (BULLISH intent)
            if latest_close > curr_sh:
                if self.trend != "BULLISH":
                    # ChoCH: trend reversal — validate with full body close
                    # ChoCH requires close above 2+ previous swing highs
                    broken_count = sum(1 for s in high_swings[-3:] if latest_close > s["price"])
                    if broken_count >= 2:
                        last_choch = "BULLISH"
                        self.consecutive_bos = 1  # First BOS of new trend
                        self.trend_confirmed = False
                        self._last_bos_level = curr_sh
                        self._last_bos_index = high_swings[-1]["bar_idx"]
                        self.trend = "BULLISH"
                        logger.debug(f"ChoCH fired: BULLISH | Broken level: {curr_sh} | BOS count: 1")
                else:
                    # BOS validation: must close above the most recent swing high, and this
                    # must be a genuinely new swing — dedupe by bar-index identity rather than
                    # float price equality (two distinct swings can share a price by coincidence,
                    # and float equality can also fail to catch a re-fire of the same swing).
                    if self._last_bos_index != high_swings[-1]["bar_idx"]:
                        last_bos = "BULLISH"
                        self.consecutive_bos += 1
                        self._last_bos_level = curr_sh
                        self._last_bos_index = high_swings[-1]["bar_idx"]
                        if self.consecutive_bos >= self.min_bos_count:
                            self.trend_confirmed = True
                        self.bos_history.append({
                            "direction": "BULLISH",
                            "price": curr_sh,
                            "index": high_swings[-1]["index"],
                            "count": self.consecutive_bos,
                        })
                        logger.debug(f"BOS fired: BULLISH | Broken level: {curr_sh} | BOS count: {self.consecutive_bos} | Trend Confirmed: {self.trend_confirmed}")

            # Check downside break (BEARISH intent)
            elif latest_close < curr_sl:
                if self.trend != "BEARISH":
                    # ChoCH: trend reversal — validate with full body close
                    broken_count = sum(1 for s in low_swings[-3:] if latest_close < s["price"])
                    if broken_count >= 2:
                        last_choch = "BEARISH"
                        self.consecutive_bos = 1
                        self.trend_confirmed = False
                        self._last_bos_level = curr_sl
                        self._last_bos_index = low_swings[-1]["bar_idx"]
                        self.trend = "BEARISH"
                        logger.debug(f"ChoCH fired: BEARISH | Broken level: {curr_sl} | BOS count: 1")
                else:
                    # BOS validation: must close below the most recent swing low, deduped by
                    # bar-index identity (see comment in the BULLISH branch above).
                    if self._last_bos_index != low_swings[-1]["bar_idx"]:
                        last_bos = "BEARISH"
                        self.consecutive_bos += 1
                        self._last_bos_level = curr_sl
                        self._last_bos_index = low_swings[-1]["bar_idx"]
                        if self.consecutive_bos >= self.min_bos_count:
                            self.trend_confirmed = True
                        self.bos_history.append({
                            "direction": "BEARISH",
                            "price": curr_sl,
                            "index": low_swings[-1]["index"],
                            "count": self.consecutive_bos,
                        })
                        logger.debug(f"BOS fired: BEARISH | Broken level: {curr_sl} | BOS count: {self.consecutive_bos} | Trend Confirmed: {self.trend_confirmed}")


        return self._result(last_bos, last_choch)

    def get_bias(self) -> str:
        """Return the current directional bias (BULLISH/BEARISH/NEUTRAL)."""
        return self.trend

    def get_last_bos_level(self) -> float | None:
        """Return the price level of the last BOS break (for retest detection)."""
        return self._last_bos_level

    def _result(self, last_bos, last_choch) -> dict[str, Any]:
        return {
            "trend": self.trend,
            "trend_confirmed": self.trend_confirmed,
            "consecutive_bos": self.consecutive_bos,
            "last_bos": last_bos,
            "last_choch": last_choch,
            "last_bos_level": self._last_bos_level,
            "swings": self.swings,
            "bos_history": self.bos_history,
        }
