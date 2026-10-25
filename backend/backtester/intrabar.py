"""
backend/backtester/intrabar.py

Which of a stop and a target was touched FIRST inside one bar, read from
one-minute bars.

Both backtesters settle a bar whose high and low reach the stop AND the
target with a deliberately conservative guess (`_resolve_sl_tp_hit`: the stop
wins if either heuristic favours it). That is the right default, and on a
strategy whose target is far away it almost never matters. On a strategy whose
target is a FRACTION of its stop it decides the result: the roboquant.dev
"Crazy ORB" port (target 0.35x the stop, 15-minute bars) had a stop and target
inside the same bar on a fifth of its trades; the guess booked it at 65% win /
-0.13R, the one-minute path at 79% / +0.07R - the template's published 81%.
(research/roboquant/REPLICATION-2026-10-20.md)

With M1 bars for the symbol, an ambiguous bar is walked minute by minute; a
minute that itself reaches both levels falls back to the same conservative
guess on that minute's OHLC, so this can only replace a guess with data, never
make one more optimistic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class IntrabarResolver:
    def __init__(self, m1: pd.DataFrame):
        if "time" in m1.columns:
            t = m1["time"].to_numpy(dtype="int64")
        else:
            t = pd.DatetimeIndex(m1.index).as_unit("s").asi8
        order = np.argsort(t, kind="stable")
        self.t = t[order]
        self.o = m1["open"].to_numpy(dtype=float)[order]
        self.h = m1["high"].to_numpy(dtype=float)[order]
        self.l = m1["low"].to_numpy(dtype=float)[order]  # noqa: E741

    def __len__(self) -> int:
        return len(self.t)

    def first_touch(self, direction: str, bar_start: float, bar_seconds: float,
                    stop_loss: float, take_profit: float) -> str | None:
        """'sl', 'tp', or None when the minutes do not settle it (no M1 data
        for the bar, or the deciding minute reached both levels)."""
        from backend.backtester.engine import _resolve_sl_tp_hit

        a = int(np.searchsorted(self.t, int(bar_start), side="left"))
        b = int(np.searchsorted(self.t, int(bar_start + bar_seconds), side="left"))
        buy = str(direction).upper() in ("BUY", "LONG", "1")
        for j in range(a, b):
            hi, lo = self.h[j], self.l[j]
            sl = lo <= stop_loss if buy else hi >= stop_loss
            tp = hi >= take_profit if buy else lo <= take_profit
            if sl and tp:
                s, t = _resolve_sl_tp_hit(direction, self.o[j], hi, lo, stop_loss, take_profit, True)
                return "sl" if s else ("tp" if t else None)
            if sl:
                return "sl"
            if tp:
                return "tp"
        return None


def resolve_with_minutes(resolver: IntrabarResolver | None, direction: str, open_p: float,
                         high: float, low: float, stop_loss: float, take_profit: float,
                         simulate_wicks: bool, bar_start: float | None,
                         bar_seconds: float | None) -> tuple[bool, bool]:
    """`_resolve_sl_tp_hit`, with an ambiguous bar settled from M1 bars when
    a resolver is given."""
    from backend.backtester.engine import _resolve_sl_tp_hit

    buy = str(direction).upper() in ("BUY", "LONG", "1")
    raw_sl = low <= stop_loss if buy else high >= stop_loss
    raw_tp = high >= take_profit if buy else low <= take_profit
    if raw_sl and raw_tp and resolver is not None and bar_start is not None and bar_seconds:
        first = resolver.first_touch(direction, bar_start, bar_seconds, stop_loss, take_profit)
        if first == "sl":
            return True, False
        if first == "tp":
            return False, True
    return _resolve_sl_tp_hit(direction, open_p, high, low, stop_loss, take_profit, simulate_wicks)
