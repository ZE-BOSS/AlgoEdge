"""
backend/strategies/core/max_hold.py

The research resolvers' time exit (synth_research.MAX_HOLD, used for SpikeFade,
RangeRevert, RangeBreakout, TrendDrift, HTF FVG Flip and Bias IFVG): a position
still open `max_hold_bars` bars after its entry bar leaves at that bar's close.

Applied through BaseStrategy.on_position_bar, so the single-symbol backtester,
the portfolio backtester and live (position_manager, via LIVE_POSITION_EXITS)
all book it the same way. Bars are COUNTED, not timed: an FX position held over
a weekend closes 288 bars after entry, as the resolver does, not 24 hours after.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.base_strategy import TradeAction
from backend.strategies.core.bars import _TF_SECONDS, _epoch_seconds


def _to_epoch(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return int(value)
    try:
        return int(pd.Timestamp(value).timestamp())  # naive values are UTC
    except (TypeError, ValueError):
        return None


def bars_held(candles: pd.DataFrame, timeframe: str, entry_time: Any) -> int | None:
    """Closed bars after the entry bar, up to and including the last candle."""
    if candles is None or len(candles) < 2:
        return None
    entry = _to_epoch(entry_time)
    if entry is None:
        return None
    t = _epoch_seconds(candles)
    step = _TF_SECONDS.get(str(timeframe).upper()) or int(np.diff(t[-10:]).min()) or 300
    entry_bar = entry - entry % step  # a live fill lands a few seconds into its bar
    return int((t >= entry_bar).sum()) - 1


class MaxHoldExit:
    """Mixin for strategies whose params carry `max_hold_bars` (0 = off)."""

    def _max_hold(self) -> int:
        return int(getattr(getattr(self, "params", None), "max_hold_bars", 0) or 0)

    @property
    def LIVE_POSITION_EXITS(self) -> bool:  # noqa: N802 — read by position_manager
        return self._max_hold() > 0

    @property
    def POSITION_BAR_WINDOW(self) -> int:  # noqa: N802 — read by both backtesters and live
        return self._max_hold() + 5

    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        hold = self._max_hold()
        if hold <= 0:
            return None
        held = bars_held(candles, timeframe, position.get("entry_time"))
        if held is None or held < hold:
            return None
        return TradeAction(ticket=int(position.get("ticket") or 0), action="CLOSE", close_reason="MAX_HOLD")
