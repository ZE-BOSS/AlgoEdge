"""
backend/strategies/core/bars.py

Candle DataFrame -> research `Bars` conversion shared by the strategies that run
research code on their window (ORB_v1, VWAP_v1). Lived in strategy_classic until
those six families were removed on 2026-09-14.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analytics import strategy_search as ss

_TF_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}


def _epoch_seconds(candles: pd.DataFrame) -> np.ndarray:
    if "time" in candles.columns:
        return candles["time"].to_numpy(dtype=np.int64)
    # as_unit('s') rather than asi8 // 10**9: asi8 is in the index's own unit, which is
    # nanoseconds on pandas 2 but microseconds (or seconds) on pandas 3.
    return pd.DatetimeIndex(candles.index).as_unit("s").asi8


def candles_to_bars(symbol: str, timeframe: str, candles: pd.DataFrame, pad: bool) -> ss.Bars:
    t = _epoch_seconds(candles).astype(np.int64)
    cols = [candles[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close")]
    vol = None
    for name in ("tick_volume", "volume", "tickvol"):
        if name in candles.columns:
            vol = candles[name].to_numpy(dtype=float)
            break
    # The bar's recorded spread, in PRICE units — the research floors every stop
    # at 4x it (edge_lab.accept_stop). Passing zeros here removed that floor live
    # and in the app, so BTCUSD session-pullback stops came out tighter than the
    # stops that were measured.
    spread = np.zeros(len(t))
    if "spread" in candles.columns:
        try:
            from backend.risk.position_sizer import get_symbol_info
            point = float(get_symbol_info(symbol).get("point") or 0.0)
            if point > 0:
                spread = np.maximum(candles["spread"].to_numpy(dtype=float), 0.0) * point
        except Exception:
            pass
    if pad and len(t):
        t = np.r_[t, t[-1] + _TF_SECONDS.get(timeframe, 3600)]
        cols = [np.r_[c, c[-1]] for c in cols]
        vol = np.r_[vol, vol[-1]] if vol is not None else None
        spread = np.r_[spread, spread[-1]]
    return ss.Bars(symbol, timeframe, t, *cols, spread, vol)
