"""
backend/strategies/core/confluence_flags.py

The M5 context confluences shared by HTFFVGFlip_v1 and BiasIFVG_v1, computed
exactly as backend/analytics/fvg_research._common_flags computes them from the
full-history frame (synth_research.build_frame), so a combination measured in
the 2026-09-14 confluence study reproduces in the engine:

    htf_trend   close on the trade's side of a 600-bar EMA that also slopes that
                way over 12 bars (needs a ~3000-bar M5 window to converge)
    adx_trend   ADX(14) >= 20            (strategy_two.calculate_adx)
    vol_high    ATR(14) >= median ATR(14) of the last 288 bars
    london      bar open 07:00-16:00 UTC
    newyork     bar open 12:30-21:00 UTC
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def m5_context_flags(candles: pd.DataFrame, direction: int, *, need_htf: bool = True,
                     need_adx: bool = True, need_vol: bool = True) -> dict[str, bool]:
    from backend.strategies.strategy_two.engine import calculate_adx, calculate_atr

    out: dict[str, bool] = {}
    close = candles["close"].to_numpy(dtype=float)
    c = float(close[-1])
    if need_htf:
        eh = candles["close"].ewm(span=600, adjust=False).mean().to_numpy()
        j = eh.size - 1
        out["htf_trend"] = bool(j >= 12 and direction * (c - eh[j]) > 0 and direction * (eh[j] - eh[j - 12]) > 0)
    if need_adx:
        a = calculate_adx(candles, 14).to_numpy()
        out["adx_trend"] = bool(np.nan_to_num(a[-1], nan=0.0) >= 20)
    if need_vol:
        atr = calculate_atr(candles, 14).to_numpy()
        hist = atr[-288:]
        med = float(np.nanmedian(hist)) if hist.size and np.isfinite(hist).any() else np.nan
        out["vol_high"] = bool(np.isfinite(med) and np.isfinite(atr[-1]) and atr[-1] - med >= 0)
    secs = int(pd.Timestamp(candles.index[-1]).timestamp()) % 86400
    out["london"] = 7 * 3600 <= secs < 16 * 3600
    out["newyork"] = 12 * 3600 + 1800 <= secs < 21 * 3600
    return out
