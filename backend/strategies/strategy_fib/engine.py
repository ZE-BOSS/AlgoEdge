"""
backend/strategies/strategy_fib/engine.py

FibTrend_v1 — the Fibonacci pullback inside a trend, with a trailing exit.

THE RULE (H4 structure, M15 execution)
--------------------------------------
1. Swings are H4 fractals with `swing_bars` bars each side; a leg is two
   consecutive alternating swings, at least `min_leg_atr` H4 ATRs long. The leg
   is known only when its end swing is confirmed, never earlier.
2. Trend filter: the confirming H4 bar closes on the trend side of the 300-bar
   (50-day) SMA, and the SMA is moving that way over 30 bars.
3. Entry: the first M15 bar after confirmation that trades back to the 0.5
   level, unless price ran beyond the leg's end first or the pullback had
   already reached 0.5 before the leg was confirmed ("missed entry, no trade").
   The app fills at the next M15 open; the setup expires after 30 H4 bars.
4. Stop at the 0.786 level. No target: a chandelier trail 3 x H4 ATR back from
   the best H4 close since entry, through `on_position_bar`.

`entry_mode="immediate"` skips the pullback and enters at the first M15 open
after the leg is confirmed (measured with stop_level 0.5) — the form that held
on real CFDs. See params.py for the numbers and research/fib/ for the study.

WHY M15 AND NOT H4
------------------
The research entered on a resting limit at 0.5. The app has no resting orders:
it fills at the next bar's open after a signal. On H4 that is up to four hours
late; on M15 it is fifteen minutes, so the fill stays close to the level.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.core.daily_atr import epoch_seconds, last_epoch
from backend.strategies.core.trail import to_epoch
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_fib import core
from backend.strategies.strategy_fib.params import FibTrendParams
from backend.utils.logger import get_logger

logger = get_logger(__name__)

TIMEFRAME = "M15"
PER_H4 = 16                    # M15 bars in an H4 bar
POSITION_H4_BARS = 200         # H4 bars the trail's ATR and best close are read from


@register_strategy("FibTrend_v1")
class FibTrendStrategy(BaseStrategy):
    strategy_id = "FibTrend_v1"
    LIVE_POSITION_EXITS = True     # the trail IS the exit
    TIMEFRAME = TIMEFRAME

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, "fib_trend", None) or FibTrendParams()
        self._cache: dict[str, tuple[int, list[dict]]] = {}

    # ── windows ──────────────────────────────────────────────────────────────
    def h4_bars_needed(self) -> int:
        p = self.params
        trend = int(p.trend_sma_bars) + int(p.trend_slope_bars) if p.trend_filter else 0
        # + the expiry (a leg confirmed that long ago can still trade) + room for
        # the swing sequence and the ATR to settle before the oldest live leg
        return max(trend, 4 * int(p.atr_bars)) + int(p.order_expiry_bars) + 120

    def get_required_timeframes(self) -> list[str]:
        return [TIMEFRAME]

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802 — read by strategies.windows
        # every bucket of the last N complete H4 bars, plus the one the window
        # may cut and the one still forming
        return {TIMEFRAME: (self.h4_bars_needed() + 2) * PER_H4}

    @property
    def POSITION_BAR_WINDOW(self) -> int:  # noqa: N802 — backtesters and live
        return (POSITION_H4_BARS + 2) * PER_H4

    async def initialize(self):
        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None

    # ── signals ──────────────────────────────────────────────────────────────
    def _setups(self, symbol: str, t: np.ndarray, candles: pd.DataFrame) -> list[dict] | None:
        last_close = int(t[-1]) + core.M15
        bucket = last_close // core.H4 * core.H4      # every complete H4 bar ends at or before this
        hit = self._cache.get(symbol)
        if hit is not None and hit[0] == bucket:
            return hit[1]
        H = core.h4_bars(t, candles["open"].to_numpy(dtype=float), candles["high"].to_numpy(dtype=float),
                         candles["low"].to_numpy(dtype=float), candles["close"].to_numpy(dtype=float),
                         last_close, self.h4_bars_needed())
        found = None if H is None else core.setups(H, self.params)
        self._cache[symbol] = (bucket, found)
        return found

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self.begin_candidate(
            symbol, timeframe,
            bar_time=candles.index[-1] if candles is not None and len(candles) else None,
        )
        if timeframe != TIMEFRAME or candles is None or len(candles) < 2:
            return None
        p = self.params
        t = epoch_seconds(candles)
        found = self._setups(symbol, t, candles)
        if not self.gate("h4_history", found is not None,
                         f"need {self.h4_bars_needed()} complete H4 bars in the M15 window"):
            return None
        if not self.gate("live_setup", bool(found), "no trend-aligned fib leg is live"):
            return None
        mode = "immediate" if p.entry_mode == "immediate" else "fib"
        hits = [s for s in found if core.triggered(s, t, candles["high"].to_numpy(dtype=float),
                                                   candles["low"].to_numpy(dtype=float), mode)]
        if not self.gate("entry_reached", bool(hits),
                         "price has not traded back to the fib entry" if mode == "fib"
                         else "no leg confirmed on this bar"):
            return None
        s = max(hits, key=lambda x: x["conf_time"])
        c = float(candles["close"].iloc[-1])
        long = s["d"] > 0
        stop = float(s["stop"])
        risk = (c - stop) if long else (stop - c)
        if not self.gate("stop_behind_price", risk > 0.02 * s["atr"],
                         f"close {c:.5f} is not clear of the {p.stop_level:g} stop {stop:.5f}"):
            return None

        from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults
        rr = float(SLOT_TP1_RR.get(f"{symbol.upper()}|{self.strategy_id}",
                                   get_strategy_defaults(self.strategy_id).get("tp1_rr", 20.0)))
        top, bottom = max(s["leg_from"], s["leg_to"]), min(s["leg_from"], s["leg_to"])
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction="BUY" if long else "SELL",
            signal_type="FIB_TREND_PULLBACK" if mode == "fib" else "FIB_TREND_LEG",
            timeframe=TIMEFRAME,
            entry_price=c,
            entry_zone_top=top,
            entry_zone_bottom=bottom,
            stop_loss=stop,
            take_profit=c + rr * risk if long else c - rr * risk,
            confluence_score=80,
            timestamp=float(last_epoch(candles)),
            metadata={
                "size_modifier": 1.0,
                "trail_method": "NONE",       # the strategy trails, not trailing_manager
                "setup": "fib_trend",
                "entry_mode": mode,
                "fib_entry": float(s["entry"]),
                "fib_stop": stop,
                "fib_end": float(s["end0"]),
                "leg_start_time": s["leg_start"],
                "leg_end_time": s["leg_end"],
                "leg_confirmed_time": s["conf_time"],
                "atr_val": float(s["atr"]),
                "tp1_rr": rr,
                "reason": (
                    f"{'up' if long else 'down'} leg {s['leg_from']:.5f} -> {s['leg_to']:.5f} with the 50-day trend; "
                    + (f"pullback to fib {p.entry_level:g} ({s['entry']:.5f})" if mode == "fib"
                       else "leg just confirmed")
                    + f"; stop at fib {p.stop_level:g}, trailing {p.trail_atr_multiple:g}x H4 ATR, no target"
                ),
            },
        ))

    # ── exit ─────────────────────────────────────────────────────────────────
    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        """Move the stop to the chandelier level. Both backtesters and live
        refuse a level that would loosen the stop, which is the ratchet."""
        if candles is None or len(candles) < 2:
            return None
        entry = to_epoch(position.get("entry_time"))
        if entry is None:
            return None
        t = epoch_seconds(candles)
        H = core.h4_bars(t, candles["open"].to_numpy(dtype=float), candles["high"].to_numpy(dtype=float),
                         candles["low"].to_numpy(dtype=float), candles["close"].to_numpy(dtype=float),
                         int(t[-1]) + core.M15, POSITION_H4_BARS)
        if H is None:
            return None
        p = self.params
        level = core.trail_level(H, entry, position.get("direction", "BUY"),
                                 float(p.trail_atr_multiple), int(p.atr_bars))
        if level is None or not np.isfinite(level):
            return None
        return TradeAction(ticket=int(position.get("ticket") or 0), action="MODIFY_SL",
                           new_sl=float(level), close_reason="TRAIL")
