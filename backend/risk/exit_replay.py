"""
backend/risk/exit_replay.py

Break-even and trailing for a LIVE trade, decided by the code both backtest
engines use, bar for bar.

Why (2026-09-15). The live position manager had its own break-even and
trailing implementation, and it disagreed with the backtester in four ways:

  * it read the GLOBAL risk settings, never a strategy's measured exits
    (SpikeFade/RangeRevert are measured with break-even OFF; live used the
    global EITHER), while both backtest engines apply them;
  * it checked every ~20 s against the tick price, where a backtest checks once
    per closed bar against that bar's close;
  * ATR trailing trailed from the current price instead of from the highest
    high / lowest low since entry, with a different ATR (simple mean including
    the forming bar) and a different structure-swing rule;
  * its TP1 break-even cascade used the live tick spread, the backtest the
    resolved cost spread.

`replay_stops` walks the closed bars since entry exactly as
backtester/engine.py's bar loop does — highest/lowest update, then
`RiskEngine.manage_open_position` with ATR and swing points from the functions
below, then the TP1 cascade through `_breakeven_stop` — and returns where each
leg's stop would be now. Replaying from entry every time makes it stateless, so
a restart or a missed management pass cannot change the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

ATR_PERIOD = 14
SWING_LOOKBACK = 20


def atr_series(high, low, close, period: int = ATR_PERIOD) -> np.ndarray:
    """ATR at bar i = mean true range of the `period` bars BEFORE bar i; 0 until
    `period` bars exist. backtester/engine.py's definition, shared by both
    engines and live."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    out = np.zeros(n)
    if n == 0:
        return out
    prev = np.roll(close, 1)
    prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    if n <= period:
        return out
    # `for i in range(period, n): out[i] = np.mean(tr[i-period:i])` is what
    # this was: one numpy call per bar over a 14-element slice, 20,000 times
    # a rollout, and a third of the run after the swing cache was fixed.
    #
    # A sliding window rather than a cumulative sum, deliberately. Both give
    # a rolling mean; a cumsum accumulates one running total and its rounding
    # drifts from what the per-slice version produced, and ATR sets stop
    # distances. This reduces each window independently, exactly as the loop
    # did, so the numbers are identical and not merely close.
    windows = np.lib.stride_tricks.sliding_window_view(tr, period)
    out[period:n] = windows[:n - period].mean(axis=1)
    return out


def swing_masks(high, low, sw_len: int) -> tuple[np.ndarray, np.ndarray]:
    """Per bar: is it a confirmed swing high / a confirmed swing low?

    WHY THIS IS SEPARATE FROM `swing_points_at`
    "Is bar j the highest of the `sw_len` bars either side of it" is a
    property of **j alone**. The bar you are standing on, i, decides only
    which range of j you look at - it does not change the answer for any j.
    `swing_points_at` was recomputing those answers from scratch on every
    bar: measured on a 20,000-bar rollout, 20,000 calls doing 600,000 numpy
    reductions between them, which was 77% of the entire run.

    So it is computed once, for every bar, as one sliding-window reduction.
    The window is `2*sw_len+1` wide and centred on j, exactly as the
    per-bar version sliced it, and only fully-contained windows exist -
    `swing_points_at` never asks about a j within `sw_len` of either end.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    n = len(high)
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    width = 2 * int(sw_len) + 1
    if n < width or sw_len < 0:
        return is_high, is_low
    # Window starting at s covers [s, s+width), whose centre is s + sw_len -
    # the same slice `high[j-sw_len : j+sw_len+1]` takes for j = s + sw_len.
    win_h = np.lib.stride_tricks.sliding_window_view(high, width)
    win_l = np.lib.stride_tricks.sliding_window_view(low, width)
    centres = slice(sw_len, n - sw_len)
    is_high[centres] = high[centres] == win_h.max(axis=1)
    is_low[centres] = low[centres] == win_l.min(axis=1)
    return is_high, is_low


def swing_points_at(high, low, i: int, sw_len: int, lookback: int = SWING_LOOKBACK,
                    masks: tuple[np.ndarray, np.ndarray] | None = None
                    ) -> list[dict[str, Any]]:
    """Confirmed swing highs/lows in the `lookback` bars before bar i — the
    engines' swing cache for bar i.

    `masks` is `swing_masks(high, low, sw_len)` computed once by a caller that
    walks every bar. Without it this computes them per call, which is correct
    but is the quadratic behaviour the masks exist to remove.
    """
    if i < lookback:
        return []
    is_high, is_low = masks if masks is not None else swing_masks(high, low, sw_len)
    points = []
    for j in range(max(sw_len, i - lookback), i - sw_len):
        if j - sw_len < 0:
            continue
        # HIGH before LOW within a bar, and bars in ascending j: the order
        # this list comes back in is what the trailing logic reads.
        if is_high[j]:
            points.append({"type": "HIGH", "price": float(high[j])})
        if is_low[j]:
            points.append({"type": "LOW", "price": float(low[j])})
    return points


def swing_length(risk_config: dict[str, Any]) -> int:
    return int(risk_config.get("trail_structure_bars", risk_config.get("swing_length", 5)))


@dataclass
class LegState:
    level: int
    stop_loss: float                 # the stop the leg was opened with, anchored to its fill
    closed_at: Any = None            # open time of the bar the leg closed in; None while open
    closed_by_target: bool = False   # it closed at its take-profit
    be_applied: bool = False
    trail_applied: bool = False


def replay_stops(*, direction: str, entry_price: float, initial_stop: float, legs: list[LegState],
                 times, high, low, close, entry_bar_time, risk_config: dict[str, Any], symbol: str,
                 spread_pips: float, risk_engine: Any = None) -> dict[int, LegState]:
    """Walk the CLOSED bars after the entry bar and return each leg's stop as a
    backtest would hold it after the last of them. `times` are bar-open times,
    ascending; the bar a trade entered in is managed from the next bar on, as in
    both engines."""
    from backend.backtester.engine import _breakeven_stop
    from backend.risk.engine import RiskEngine
    from backend.risk.multi_tp import MultiTPManager, _is_buy
    from backend.risk.position_sizer import get_pip_size

    engine = risk_engine or RiskEngine(risk_config)
    trail_methods = MultiTPManager(risk_config).trail_methods
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    atr = atr_series(high, low, close)
    sw_len = swing_length(risk_config)
    # Once for the whole replay, not once per bar.
    masks = swing_masks(high, low, sw_len)
    pip = get_pip_size(symbol)
    buy = _is_buy(direction)
    be_mode = risk_config.get("be_mode", "EITHER")

    state = {leg.level: leg for leg in legs}
    pos = {
        leg.level: {
            "direction": "BUY" if buy else "SELL", "entry_price": float(entry_price),
            "stop_loss": float(leg.stop_loss), "initial_stop_loss": float(initial_stop),
            "original_sl": float(initial_stop), "symbol": symbol, "tp_level": leg.level,
            "be_applied": False, "trail_applied": False,
            "trail_method": trail_methods[leg.level - 1] if 0 < leg.level <= len(trail_methods) else None,
            "highest_price": float(entry_price), "lowest_price": float(entry_price),
        }
        for leg in legs
    }

    times = np.asarray(times)
    start = int(np.searchsorted(times, np.asarray(entry_bar_time, dtype=times.dtype), side="right"))
    for i in range(start, len(times)):
        t = times[i]
        alive = [lv for lv, leg in state.items() if leg.closed_at is None or t < leg.closed_at]
        if not alive:
            break
        tp1_closing = any(leg.level == 1 and leg.closed_by_target and leg.closed_at == t for leg in state.values())
        swings = None
        for lv in alive:
            p = pos[lv]
            if buy:
                p["highest_price"] = max(p["highest_price"], high[i])
            else:
                p["lowest_price"] = min(p["lowest_price"], low[i])
            if lv != 1 and tp1_closing:
                continue  # the engines defer siblings to the cascade on the bar TP1 closes
            if swings is None:
                swings = swing_points_at(high, low, i, sw_len, masks=masks)
            for action in engine.manage_open_position(p, float(close[i]), atr_value=float(atr[i]),
                                                      swing_points=swings):
                if action.get("action") != "MODIFY_SL":
                    continue
                p["stop_loss"] = action["new_sl"]
                if action.get("reason") == "BREAKEVEN":
                    p["be_applied"] = True
                elif action.get("reason") == "TRAIL":
                    p["trail_applied"] = True
        if tp1_closing and be_mode in ("TP_HIT", "EITHER"):
            for lv in alive:
                if lv == 1:
                    continue
                p = pos[lv]
                new_sl = _breakeven_stop(direction=p["direction"], entry_price=p["entry_price"],
                                         current_price=float(close[i]), pip_size=pip, atr=float(atr[i]),
                                         risk_config=risk_config, spread_pips=spread_pips)
                if (buy and new_sl > p["stop_loss"]) or (not buy and new_sl < p["stop_loss"]):
                    p["stop_loss"] = new_sl
                    p["be_applied"] = True

    for lv, leg in state.items():
        leg.stop_loss = pos[lv]["stop_loss"]
        leg.be_applied = pos[lv]["be_applied"]
        leg.trail_applied = pos[lv]["trail_applied"]
    return state
