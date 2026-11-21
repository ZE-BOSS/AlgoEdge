"""
backend/strategies/strategy_roboquant/engine_quant.py

Strategies from the 21 Nov 2026 quant-lab research (scripts/quant_lab.py), built
on the same stateless window framework as the roboquant ports.

    QL_IBS_v1   IBS mean reversion at the US cash close   M30 (US index CFDs)

Everything is recomputed from the window on every call (the US cash-session
daily bars are rebuilt from M30 bars), so the live feed and a backtest that
hand the engine the same windows cannot disagree.
"""

from __future__ import annotations

import numpy as np

from backend.strategies.registry import register_strategy
from backend.strategies.strategy_roboquant import common as c
from backend.strategies.strategy_roboquant.engine import _RQBase
from backend.strategies.strategy_roboquant.params import QLIBSParams

CASH_OPEN, LAST_BAR = 930, 1530          # M30 bar OPEN times: 09:30 ... 15:30 (closes 16:00)


def cash_sessions(candles, ibs_below: float, sma_days: int):
    """Completed US cash sessions in the window, oldest first:
    (day, close, signal) where signal = IBS < ibs_below and close > SMA(sma_days)."""
    t = c.epochs(candles)
    day, hhmm = c.et_parts(t)
    o = candles["open"].to_numpy(dtype=float)
    h = candles["high"].to_numpy(dtype=float)
    lo = candles["low"].to_numpy(dtype=float)
    cl = candles["close"].to_numpy(dtype=float)
    m = (hhmm >= CASH_OPEN) & (hhmm <= LAST_BAR)
    days, closes, ibs = [], [], []
    if not m.any():
        return days, np.array([]), np.array([], dtype=bool)
    idx = np.flatnonzero(m)
    d = day[idx]
    cut = np.flatnonzero(np.diff(d)) + 1
    for grp in np.split(idx, cut):
        if hhmm[grp[-1]] != LAST_BAR or len(grp) < 12:      # session not finished / gappy
            continue
        H, L, C = h[grp].max(), lo[grp].min(), cl[grp[-1]]
        days.append(int(day[grp[0]]))
        closes.append(C)
        ibs.append((C - L) / (H - L) if H > L else 0.5)
    closes = np.asarray(closes)
    ibs = np.asarray(ibs)
    sig = np.zeros(len(closes), dtype=bool)
    for k in range(sma_days - 1, len(closes)):
        sig[k] = ibs[k] < ibs_below and closes[k] > closes[k - sma_days + 1:k + 1].mean()
    return days, closes, sig


@register_strategy("QL_IBS_v1")
class QLIBSMeanReversion(_RQBase):
    strategy_id = "QL_IBS_v1"
    PARAMS_ATTR = "ql_ibs"
    PARAMS_CLS = QLIBSParams
    # 200 sessions of 23-hour M30 bars (~46/day) plus slack for holidays
    WINDOW = 10500
    POSITION_BAR_WINDOW = 10500

    def _today(self, candles):
        """(signal today, today's ET day) when the last bar is the 15:30 bar, else (None, None)."""
        t = c.epochs(candles)
        if c.et_hhmm_one(t[-1]) != LAST_BAR:
            return None, None
        p = self.params
        days, _, sig = cash_sessions(candles, p.ibs_below, p.sma_days)
        today = int(c.et_parts(t[-1:])[0][0])
        if not days or days[-1] != today:
            return None, None
        return bool(sig[-1]), today

    async def on_bar(self, symbol, timeframe, candles):
        p = self.params
        if not self._start(symbol, timeframe, candles, 20):
            return None
        sig, today = self._today(candles)
        if sig is None:
            return None
        if not self.gate("ibs_signal", sig, f"no IBS<{p.ibs_below} above SMA{p.sma_days} at the {today} close"):
            return None
        entry = float(candles["close"].iloc[-1])
        sl = entry * (1.0 - p.disaster_stop_pct / 100.0)
        meta = {"session": str(today)}
        if p.notional_x and p.notional_x > 0:
            meta["size_notional_x"] = float(p.notional_x)
        return self._signal(symbol, timeframe, candles, "BUY", entry, sl, entry + 40.0 * (entry - sl), **meta)

    def on_position_bar(self, symbol, timeframe, candles, position):
        """At each later cash close: hold while the signal repeats, else close."""
        held = self._bars_held(candles, position)
        if held is not None and held < 1:
            return None
        sig, _ = self._today(candles)
        if sig is None or sig:
            return None
        return self._close(position, "QL_IBS_EXIT")
