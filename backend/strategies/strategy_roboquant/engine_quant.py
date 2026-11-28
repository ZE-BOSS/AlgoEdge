"""
backend/strategies/strategy_roboquant/engine_quant.py

Strategies from the 21 Nov 2026 quant-lab research (scripts/quant_lab.py), built
on the same stateless window framework as the roboquant ports.

    QL_IBS_v1     IBS mean reversion at the US cash close            M30 (US index CFDs)
    QL_DipBuy_v1  IBS / cumulative RSI(2) / 3-down union, SMA5 exit  M30 (US index CFDs)

Everything is recomputed from the window on every call (the US cash-session
daily bars are rebuilt from M30 bars), so the live feed and a backtest that
hand the engine the same windows cannot disagree.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.core.trail import to_epoch
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_roboquant import common as c
from backend.strategies.strategy_roboquant.engine import _RQBase
from backend.strategies.strategy_roboquant.params import QLDipBuyParams, QLIBSParams

CASH_OPEN, LAST_BAR = 930, 1530          # M30 bar OPEN times: 09:30 ... 15:30 (closes 16:00)


def session_ohlc(candles):
    """Completed US cash sessions (09:30-16:00 ET) in the window, oldest first:
    (days, highs, lows, closes). A session counts once its 15:30 bar is in."""
    t = c.epochs(candles)
    day, hhmm = c.et_parts(t)
    h = candles["high"].to_numpy(dtype=float)
    lo = candles["low"].to_numpy(dtype=float)
    cl = candles["close"].to_numpy(dtype=float)
    m = (hhmm >= CASH_OPEN) & (hhmm <= LAST_BAR)
    days, H, L, C = [], [], [], []
    if m.any():
        idx = np.flatnonzero(m)
        cut = np.flatnonzero(np.diff(day[idx])) + 1
        for grp in np.split(idx, cut):
            if hhmm[grp[-1]] != LAST_BAR or len(grp) < 12:      # session not finished / gappy
                continue
            days.append(int(day[grp[0]]))
            H.append(h[grp].max())
            L.append(lo[grp].min())
            C.append(cl[grp[-1]])
    return days, np.asarray(H, dtype=float), np.asarray(L, dtype=float), np.asarray(C, dtype=float)


def _sma_above(closes: np.ndarray, n: int) -> np.ndarray:
    """closes[k] > mean(closes[k-n+1 .. k]); False until n sessions exist."""
    out = np.zeros(len(closes), dtype=bool)
    if n <= 0:
        return np.ones(len(closes), dtype=bool)
    if len(closes) >= n:
        cs = np.concatenate([[0.0], np.cumsum(closes)])
        mean = (cs[n:] - cs[:-n]) / n
        out[n - 1:] = closes[n - 1:] > mean
    return out


def _ibs(H, L, C):
    rng = H - L
    return np.where(rng > 0, (C - L) / np.where(rng > 0, rng, 1.0), 0.5)


def wilder_rsi(closes: np.ndarray, n: int) -> np.ndarray:
    """RSI with Wilder smoothing (alpha 1/n, seeded with the first change), as
    pandas ewm(adjust=False) in the research lab. NaN for the first close."""
    out = np.full(len(closes), np.nan)
    if len(closes) < 2:
        return out
    d = np.diff(closes)
    up = dn = None
    a = 1.0 / n
    for k, x in enumerate(d, start=1):
        u, w = max(x, 0.0), max(-x, 0.0)
        up = u if up is None else up + a * (u - up)
        dn = w if dn is None else dn + a * (w - dn)
        out[k] = 100.0 if dn == 0 and up > 0 else (50.0 if dn == 0 else 100.0 - 100.0 / (1.0 + up / dn))
    return out


def cash_sessions(candles, ibs_below: float, sma_days: int):
    """Completed US cash sessions in the window, oldest first:
    (day, close, signal) where signal = IBS < ibs_below and close > SMA(sma_days)."""
    days, H, L, C = session_ohlc(candles)
    if not days:
        return days, np.array([]), np.array([], dtype=bool)
    sig = (_ibs(H, L, C) < ibs_below) & _sma_above(C, sma_days)
    return days, C, sig


def dipbuy_signals(H, L, C, p) -> np.ndarray:
    """Union of the enabled dip signals, each gated by close > SMA(sma_days)."""
    trend = _sma_above(C, p.sma_days)
    sig = np.zeros(len(C), dtype=bool)
    if p.ibs_below and p.ibs_below > 0:
        sig |= _ibs(H, L, C) < p.ibs_below
    if p.use_cum_rsi and len(C) >= 3:
        r2 = wilder_rsi(C, 2)
        cum = np.full(len(C), np.nan)
        cum[1:] = r2[1:] + r2[:-1]
        with np.errstate(invalid="ignore"):
            sig |= np.nan_to_num(cum, nan=1e9) < p.cum_rsi_below
    if p.use_down_days and p.down_days > 0 and len(C) > p.down_days:
        lower = np.zeros(len(C), dtype=bool)
        lower[1:] = C[1:] < C[:-1]
        run = np.zeros(len(C), dtype=int)
        for k in range(1, len(C)):
            run[k] = run[k - 1] + 1 if lower[k] else 0
        sig |= run >= p.down_days
    return sig & trend


def partial_today(candles):
    """Completed sessions plus TODAY so far, when the last closed bar is the
    15:00 ET bar (the half-hour before the decision bar): (H, L, C) arrays with
    today last. None at any other time. Used for pre-signal alerts only."""
    t = c.epochs(candles)
    if c.et_hhmm_one(t[-1]) != LAST_BAR - 30:
        return None
    days, H, L, C = session_ohlc(candles)
    day, hhmm = c.et_parts(t)
    today = int(day[-1])
    if days and days[-1] == today:
        return None
    m = (day == today) & (hhmm >= CASH_OPEN) & (hhmm <= LAST_BAR - 30)
    if m.sum() < 10:
        return None
    h = float(candles["high"].to_numpy(dtype=float)[m].max())
    lo = float(candles["low"].to_numpy(dtype=float)[m].min())
    cl = float(candles["close"].to_numpy(dtype=float)[-1])
    return np.append(H, h), np.append(L, lo), np.append(C, cl)


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

    def watch(self, symbol, timeframe, candles):
        """At 15:30 ET, half an hour before the decision: if the session so far
        already qualifies, the close is likely to (pre-signal alerts)."""
        p = self.params
        if timeframe != p.timeframe or candles is None or len(candles) < 40:
            return []
        part = partial_today(candles)
        if part is None:
            return []
        H, L, C = part
        if not (_ibs(H[-1:], L[-1:], C[-1:])[0] < p.ibs_below and _sma_above(C, p.sma_days)[-1]):
            return []
        level = float(L[-1] + p.ibs_below * (H[-1] - L[-1]))
        return [{"direction": "BUY", "level": level, "cross": "below",
                 "note": f"decided at the 16:00 New York close: IBS now {(_ibs(H[-1:], L[-1:], C[-1:])[0]):.2f}, "
                         f"needs under {p.ibs_below}; a close below {level:.2f} keeps it"}]

    def on_position_bar(self, symbol, timeframe, candles, position):
        """At each later cash close: hold while the signal repeats, else close."""
        held = self._bars_held(candles, position)
        if held is not None and held < 1:
            return None
        sig, _ = self._today(candles)
        if sig is None or sig:
            return None
        return self._close(position, "QL_IBS_EXIT")


@register_strategy("QL_DipBuy_v1")
class QLDipBuy(_RQBase):
    strategy_id = "QL_DipBuy_v1"
    PARAMS_ATTR = "ql_dipbuy"
    PARAMS_CLS = QLDipBuyParams
    WINDOW = 10500
    POSITION_BAR_WINDOW = 10500

    @staticmethod
    def _session_today(candles):
        t = c.epochs(candles)
        if c.et_hhmm_one(t[-1]) != LAST_BAR:
            return None, None
        days, H, L, C = session_ohlc(candles)
        today = int(c.et_parts(t[-1:])[0][0])
        if not days or days[-1] != today:
            return None, None
        return (days, H, L, C), today

    async def on_bar(self, symbol, timeframe, candles):
        p = self.params
        if not self._start(symbol, timeframe, candles, 20):
            return None
        sess, today = self._session_today(candles)
        if sess is None:
            return None
        _, H, L, C = sess
        sig = bool(dipbuy_signals(H, L, C, p)[-1])
        if not self.gate("dip_signal", sig, f"no IBS / cumRSI / {p.down_days}-down dip above SMA{p.sma_days} at the {today} close"):
            return None
        entry = float(candles["close"].iloc[-1])
        sl = entry * (1.0 - p.disaster_stop_pct / 100.0)
        meta = {"session": str(today)}
        if p.notional_x and p.notional_x > 0:
            meta["size_notional_x"] = float(p.notional_x)
        return self._signal(symbol, timeframe, candles, "BUY", entry, sl, entry + 40.0 * (entry - sl), **meta)

    def watch(self, symbol, timeframe, candles):
        """At 15:30 ET: would today's close give a dip signal if the market
        closed here? (pre-signal alerts)."""
        p = self.params
        if timeframe != p.timeframe or candles is None or len(candles) < 40:
            return []
        part = partial_today(candles)
        if part is None:
            return []
        H, L, C = part
        if not bool(dipbuy_signals(H, L, C, p)[-1]):
            return []
        trend = _sma_above(C, p.sma_days)[-1]
        why, level = [], None
        if p.ibs_below and _ibs(H[-1:], L[-1:], C[-1:])[0] < p.ibs_below:
            level = float(L[-1] + p.ibs_below * (H[-1] - L[-1]))
            why.append(f"IBS under {p.ibs_below}")
        if p.use_down_days and len(C) > p.down_days and all(C[-k] < C[-k - 1] for k in range(1, p.down_days + 1)):
            level = max(level or 0.0, float(C[-2]))
            why.append(f"{p.down_days} lower closes")
        if not why:
            why.append("cumulative RSI(2) low")
        if level is None:
            level = float(C[-1])
        return [{"direction": "BUY", "level": level, "cross": "below",
                 "note": f"decided at the 16:00 New York close: {', '.join(why)}" + ("" if trend else " (trend filter off)")}]

    def on_position_bar(self, symbol, timeframe, candles, position):
        """At each later cash close: exit above SMA(exit_sma_days) or after max_hold_days sessions."""
        p = self.params
        sess, today = self._session_today(candles)
        if sess is None:
            return None
        days, _, _, C = sess
        entry_day = self._entry_day(position)
        if entry_day is not None and today <= entry_day:
            return None
        held = sum(1 for d in days if d > entry_day) if entry_day is not None else None
        n = max(1, int(p.exit_sma_days))
        if len(C) >= n and C[-1] > C[-n:].mean():
            return self._close(position, "QL_DIP_SMA_EXIT")
        if held is not None and p.max_hold_days and held >= p.max_hold_days:
            return self._close(position, "QL_DIP_TIME_EXIT")
        return None

    @staticmethod
    def _entry_day(position):
        entry = position.get("entry_time")
        if entry is None:
            return None
        ts = to_epoch(entry)              # np.int64 from the portfolio backtester too
        if ts is None:
            return None
        return int(c.et_parts(np.array([ts]))[0][0])
