"""Independent forward-excursion engine — measures the MARKET, not the backtest.

Pulls bars straight from MT5 and asks, at every single bar, a strategy-free
question:

    if I entered here with a stop at k x ATR, how far would price have gone in
    my favour BEFORE hitting that stop?

Everything downstream — best stop distance, best R:R, whether partials pay,
whether a confluence is worth anything — is a slice of that one measurement.

Stop-aware by construction: an excursion only counts if it happened before the
stop was touched. Costs are charged from the live symbol spread.

Public API
----------
load_bars(sym, tf, from_dt, to_dt)      -> dict of numpy arrays
atr(high, low, close, n)                -> ndarray
excursion(bars, direction, stop_mult, horizon, spread_pts)
    -> (max_r, hit_stop, bars_to_peak) per bar, all stop-aware
"""
from __future__ import annotations
from datetime import datetime, timezone
import numpy as np
import MetaTrader5 as mt5

TF = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
      "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
      "D1": mt5.TIMEFRAME_D1}


def load_bars(sym: str, tf: str, from_dt: datetime, to_dt: datetime) -> dict | None:
    info = mt5.symbol_info(sym)
    if info is None:
        return None
    if not info.visible:
        mt5.symbol_select(sym, True)
    r = mt5.copy_rates_range(sym, TF[tf], from_dt, to_dt)
    if r is None or len(r) < 500:
        return None
    return {
        "time": r["time"].astype(np.int64),
        "open": r["open"].astype(float),
        "high": r["high"].astype(float),
        "low": r["low"].astype(float),
        "close": r["close"].astype(float),
        "volume": r["tick_volume"].astype(float),
        "point": info.point,
        "spread_pts": float(info.spread),
        "digits": info.digits,
    }


def atr(high, low, close, n: int = 14) -> np.ndarray:
    prev = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    out = np.full(len(tr), np.nan)
    if len(tr) < n:
        return out
    # Wilder smoothing
    seed = tr[:n].mean()
    out[n - 1] = seed
    a = seed
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def excursion(bars: dict, direction: str, stop_mult: float, horizon: int,
              cost_r_frac: float = 0.0, atr_arr: np.ndarray | None = None):
    """Stop-aware forward excursion in R, for an entry at every bar's close.

    Returns
    -------
    max_r        best R reached before the stop was hit (0 if stopped immediately)
    hit_stop     True if the stop was touched inside the horizon
    bars_to_peak bars taken to reach max_r
    valid        bars where the measurement is defined
    """
    o, h, l, c = bars["open"], bars["high"], bars["low"], bars["close"]
    n = len(c)
    a = atr_arr if atr_arr is not None else atr(h, l, c)
    risk = stop_mult * a                                  # price distance = 1R
    buy = direction == "BUY"
    entry = c
    stop = entry - risk if buy else entry + risk

    max_r = np.zeros(n)
    hit_stop = np.zeros(n, dtype=bool)
    peak_at = np.zeros(n, dtype=int)
    alive = np.ones(n, dtype=bool)
    valid = np.isfinite(risk) & (risk > 0)
    alive &= valid

    for step in range(1, horizon + 1):
        idx = np.arange(n) + step
        ok = idx < n
        idx_c = np.clip(idx, 0, n - 1)
        fh, fl = h[idx_c], l[idx_c]

        # stop check first — a bar that takes out the stop ends the trade, and
        # anything the same bar reached afterwards must not be credited
        if buy:
            stopped = alive & ok & (fl <= stop)
            fav = (fh - entry) / np.where(risk > 0, risk, np.nan)
        else:
            stopped = alive & ok & (fh >= stop)
            fav = (entry - fl) / np.where(risk > 0, risk, np.nan)

        live = alive & ok & ~stopped
        better = live & (fav > max_r)
        max_r = np.where(better, fav, max_r)
        peak_at = np.where(better, step, peak_at)

        hit_stop |= stopped
        alive &= ~stopped & ok

    max_r = np.maximum(max_r, 0.0)
    if cost_r_frac:
        max_r = np.maximum(max_r - cost_r_frac, 0.0)
    return max_r, hit_stop, peak_at, valid


def cost_in_r(bars: dict, stop_mult: float, atr_arr: np.ndarray) -> float:
    """Round-trip spread as a fraction of 1R, at the median bar."""
    spread_price = bars["spread_pts"] * bars["point"]
    risk = stop_mult * atr_arr
    risk = risk[np.isfinite(risk) & (risk > 0)]
    if not len(risk):
        return 0.0
    return float(spread_price / np.median(risk))


def expectancy(max_r: np.ndarray, valid: np.ndarray, tp: float, cost: float) -> dict:
    """Expectancy of a fixed target at `tp` R, given stop-aware excursions."""
    m = max_r[valid]
    if not len(m):
        return {}
    win = m >= tp
    exp = np.where(win, tp - cost, -1.0 - cost).mean()
    gp = (tp - cost) * win.sum()
    gl = (1.0 + cost) * (~win).sum()
    return {"n": int(len(m)), "hit": float(win.mean()), "exp_r": float(exp),
            "pf": float(gp / gl) if gl else float("inf")}
