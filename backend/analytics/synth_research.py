"""
backend/analytics/synth_research.py

SpikeFade_v1, RangeRevert_v1, RangeBreakout_v1 and TrendDrift_v1, stripped to
their confluences — the same treatment VWAP and APA got on 2026-09-13.

HOW IT MIRRORS THE LIVE ENGINE (backend/strategies/strategy_synth/engine.py)
---------------------------------------------------------------------------
* Indicators are the engine's: EMA(fast/slow) with adjust=False, ATR(14) as a
  SIMPLE rolling mean of true range (strategy_two._rolling_mean/_true_range,
  first TR = high-low), ADX(14) from the same rolling means. All of these are
  window-independent once 14 bars exist, and the EMAs converge inside the
  engine's 500-bar window (seed weight e^-10 at span 50), so computing them
  over the full history gives the engine's values.
* The entry predicates are copied line for line from each engine's
  `signal_for_bar`, evaluated on the LAST CLOSED bar.
* Entry is the NEXT bar's open: the route stamps the signal on the last closed
  bar and the backtest engine fills at the first bar opening after it, which is
  also live's price.
* Stop = stop_atr x ATR from entry, target = rr x stop, one position at a time,
  at most `max_per_day` entries per UTC day (the engine's daily budget: 4 at 1%
  risk with max_daily_risk_pct 4).

EVERY OPTIONAL CONFLUENCE IS RECORDED, NOT ENFORCED
---------------------------------------------------
    trend_with      EMA fast/slow regime points the trade's way
    htf_trend       close on the trade's side of a 600-bar M5 EMA (~50 H1 bars)
                    that is also sloping that way over 12 bars — ORB_v1's shipped
                    trend confluence (edge_lab.htf_trend)
    adx_trend       ADX(14) >= 20
    adx_range       ADX(14) < 20
    vol_high        ATR(14) at or above its 288-bar (1 day) median
    candle_confirm  the signal bar closed in the trade's direction
    strong_close    the signal bar closed in its outer 30% on the trade's side
    london          signal bar between 07:00 and 16:00 UTC
    newyork         signal bar between 12:30 and 21:00 UTC

OUTCOMES
--------
Bar walk from the entry bar: a stop or target the bar OPENS beyond fills at that
open; a bar touching both loses; the bar's own spread is charged once at entry;
stops hit intrabar pay the app's measured overshoot (fill_model profiles — absolute
index points on Crash 1000/300, a fraction of the stop elsewhere); 288 bars (24h)
maximum hold, closed at that bar's close.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

STRATEGIES = ("SpikeFade_v1", "RangeRevert_v1", "RangeBreakout_v1", "TrendDrift_v1")
CONFLUENCES = ("trend_with", "htf_trend", "adx_trend", "adx_range", "vol_high",
               "candle_confirm", "strong_close", "london", "newyork")
STOP_ATR = (1.0, 2.5, 5.0)
RR = (1.0, 1.5, 2.0, 3.0, 5.0, 8.0)
MAX_HOLD = 288
SIGNAL_GRID: dict[str, dict[str, tuple]] = {
    "SpikeFade_v1": {"spike_k_atr": (2.0, 3.0, 4.0, 5.0)},
    "RangeRevert_v1": {"revert_k_atr": (1.5, 2.0, 3.0)},
    "RangeBreakout_v1": {"breakout_lookback": (10, 20, 55)},
    "TrendDrift_v1": {"min_adx_to_trade": (0, 20, 25)},
}


# ── indicators, identical arithmetic to strategy_two ─────────────────────────

def true_range(h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> np.ndarray:
    prev_c = np.r_[np.nan, c[:-1]]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))
    if tr.size:
        tr[0] = h[0] - lo[0]
    return tr


def rolling_mean(a: np.ndarray, period: int) -> np.ndarray:
    n = a.size
    out = np.full(n, np.nan)
    if n < period:
        return out
    valid = ~np.isnan(a)
    filled = np.where(valid, a, 0.0)
    csum = np.concatenate(([0.0], np.cumsum(filled)))
    ccnt = np.concatenate(([0], np.cumsum(valid)))
    ws = csum[period:] - csum[:-period]
    wc = ccnt[period:] - ccnt[:-period]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[period - 1:] = np.where(wc == period, ws / period, np.nan)
    return out


def ema(x: np.ndarray, span: int) -> np.ndarray:
    """pandas ewm(span, adjust=False) — the engine's own call, so identical arithmetic."""
    import pandas as pd
    return pd.Series(np.asarray(x, dtype=float)).ewm(span=span, adjust=False).mean().to_numpy()


def adx(h: np.ndarray, lo: np.ndarray, c: np.ndarray, period: int = 14) -> np.ndarray:
    up = np.r_[np.nan, h[1:] - h[:-1]]
    dn = np.r_[np.nan, lo[:-1] - lo[1:]]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_ = rolling_mean(rolling_mean(true_range(h, lo, c), 1), period)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * rolling_mean(plus_dm, period) / atr_
        mdi = 100.0 * rolling_mean(minus_dm, period) / atr_
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)
    return rolling_mean(dx, period)


def rolling_median(a: np.ndarray, period: int) -> np.ndarray:
    """Median of the last `period` values (NaN-skipping, like np.nanmedian over the
    window, once at least one value is present). pandas' C rolling median: the
    masked-array version was the slowest step of a whole market's build."""
    import pandas as pd
    return pd.Series(np.asarray(a, dtype=float)).rolling(period, min_periods=1).median().to_numpy()         if a.size >= period else np.full(a.size, np.nan)


@dataclass
class Frame:
    symbol: str
    time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    spread: np.ndarray
    ema_f: np.ndarray
    ema_s: np.ndarray
    ema_h: np.ndarray
    atr: np.ndarray
    adx: np.ndarray
    atr_med: np.ndarray


def build_frame(symbol: str, bars: dict[str, np.ndarray], ema_fast: int = 20, ema_slow: int = 50) -> Frame:
    h, lo, c = bars["high"], bars["low"], bars["close"]
    atr_ = rolling_mean(true_range(h, lo, c), 14)
    return Frame(symbol, bars["time"].astype(np.int64), bars["open"], h, lo, c, bars["spread"],
                 ema(c, ema_fast), ema(c, ema_slow), ema(c, 600), atr_, adx(h, lo, c, 14),
                 rolling_median(atr_, 288))


# ── entry predicates, copied from each engine's signal_for_bar ───────────────

def raw_signals(f: Frame, strategy: str, p: dict[str, Any]) -> np.ndarray:
    """+1 / -1 / 0 per bar, evaluated on that bar as the LAST closed bar."""
    n = f.close.size
    d = np.zeros(n, dtype=np.int8)
    a = f.atr
    ok = np.isfinite(a) & (a > 0)
    idx = np.arange(n)
    # engine: len(candles) >= max(60, lookback + 5), where len = bar index + 1
    lookback = int(p.get("breakout_lookback", 20))
    ok &= idx >= max(60, lookback + 5) - 1
    if strategy == "SpikeFade_v1":
        k = float(p["spike_k_atr"])
        with np.errstate(invalid="ignore", divide="ignore"):
            up = (f.high - f.open) / a
            dn = (f.open - f.low) / a
        short = ok & (up >= k) & (up >= dn)
        long_ = ok & ~short & (dn >= k)
        d[short], d[long_] = -1, 1
    elif strategy == "RangeRevert_v1":
        k = float(p["revert_k_atr"])
        gap = f.close - f.ema_s
        d[ok & (gap > k * a)] = -1
        d[ok & (-gap > k * a)] = 1
    elif strategy == "RangeBreakout_v1":
        nlook = int(p["breakout_lookback"])
        from numpy.lib.stride_tricks import sliding_window_view
        ph = np.full(n, np.nan)
        pl = np.full(n, np.nan)
        if n > nlook:
            ph[nlook:] = sliding_window_view(f.high, nlook).max(axis=1)[:-1]
            pl[nlook:] = sliding_window_view(f.low, nlook).min(axis=1)[:-1]
        d[ok & (f.close > ph)] = 1
        d[ok & ~(f.close > ph) & (f.close < pl)] = -1
    elif strategy == "TrendDrift_v1":
        min_adx = float(p["min_adx_to_trade"])
        sep = np.abs(f.ema_f - f.ema_s)
        base = ok & (sep > 0.2 * a)
        if min_adx > 0:
            base &= np.nan_to_num(f.adx, nan=0.0) >= min_adx
        d[base & (f.ema_f > f.ema_s) & (f.close >= f.ema_f)] = 1
        d[base & (f.ema_f < f.ema_s) & (f.close <= f.ema_f)] = -1
    else:
        raise ValueError(strategy)
    return d


def features(f: Frame, i: np.ndarray, d: np.ndarray) -> dict[str, np.ndarray]:
    c, o, h, lo = f.close[i], f.open[i], f.high[i], f.low[i]
    rng = np.maximum(h - lo, 1e-12)
    eh_prev = f.ema_h[np.maximum(i - 12, 0)]
    secs = f.time[i] % 86400
    adx_i = np.nan_to_num(f.adx[i], nan=0.0)
    return {
        "trend_with": d * (f.ema_f[i] - f.ema_s[i]) > 0,
        "htf_trend": (i >= 12) & (d * (c - f.ema_h[i]) > 0) & (d * (f.ema_h[i] - eh_prev) > 0),
        "adx_trend": adx_i >= 20,
        "adx_range": adx_i < 20,
        "vol_high": np.nan_to_num(f.atr[i] - f.atr_med[i], nan=-1.0) >= 0,
        "candle_confirm": d * (c - o) > 0,
        "strong_close": np.where(d > 0, (c - lo) / rng >= 0.7, (h - c) / rng >= 0.7),
        "london": (secs >= 7 * 3600) & (secs < 16 * 3600),
        "newyork": (secs >= 12 * 3600 + 1800) & (secs < 21 * 3600),
    }


# ── outcome resolution ───────────────────────────────────────────────────────

def _resolver():
    try:
        from numba import njit
    except Exception:  # pragma: no cover - numba optional
        def njit(*a, **k):
            return (lambda fn: fn) if not a or not callable(a[0]) else a[0]

    @njit(cache=True)
    def resolve(open_, high, low, close, spread, entries, dirs, stop_dists, rrs,
                max_hold, ovs_abs, ovs_frac, spike_side, lam):
        m = entries.size
        k = rrs.size
        n = close.size
        r_out = np.zeros((m, k))
        exit_idx = np.zeros((m, k), dtype=np.int64)
        reason = np.zeros((m, k), dtype=np.int8)  # 1 stop 2 target 3 time 4 stop_gap 5 target_gap
        for a in range(m):
            e = entries[a]
            d = dirs[a]
            sd = stop_dists[a]
            entry = open_[e]
            cost = spread[e] / sd
            stop = entry - d * sd
            end = min(n - 1, e + max_hold)
            for q in range(k):
                tp = entry + d * rrs[q] * sd
                done = False
                for j in range(e, end + 1):
                    o, hh, ll = open_[j], high[j], low[j]
                    if j > e:
                        if (d > 0 and o <= stop) or (d < 0 and o >= stop):
                            r_out[a, q] = d * (o - entry) / sd - cost
                            exit_idx[a, q] = j
                            reason[a, q] = 4
                            done = True
                            break
                        if (d > 0 and o >= tp) or (d < 0 and o <= tp):
                            r_out[a, q] = d * (o - entry) / sd - cost
                            exit_idx[a, q] = j
                            reason[a, q] = 5
                            done = True
                            break
                    if (d > 0 and ll <= stop) or (d < 0 and hh >= stop):
                        if spike_side != 0:
                            # tick-calibrated jump fills (fill_model.SPIKE_FILLS)
                            on_spike = spike_side == 2 or (spike_side == -1 and d > 0) or (spike_side == 1 and d < 0)
                            if on_spike:
                                px = stop - lam * (stop - ll) if d > 0 else stop + lam * (hh - stop)
                            else:
                                px = stop
                            r_out[a, q] = d * (px - entry) / sd - cost
                        else:
                            # expected overshoot, clamped to the bar's own extreme
                            room = (stop - ll) if d > 0 else (hh - stop)
                            ov = ovs_abs if ovs_abs > 0 else ovs_frac * sd
                            if ov > room:
                                ov = room
                            r_out[a, q] = -1.0 - ov / sd - cost
                        exit_idx[a, q] = j
                        reason[a, q] = 1
                        done = True
                        break
                    if (d > 0 and hh >= tp) or (d < 0 and ll <= tp):
                        r_out[a, q] = rrs[q] - cost
                        exit_idx[a, q] = j
                        reason[a, q] = 2
                        done = True
                        break
                if not done:
                    r_out[a, q] = d * (close[end] - entry) / sd - cost
                    exit_idx[a, q] = end
                    reason[a, q] = 3
        return r_out, exit_idx, reason

    return resolve


_RESOLVE = None


def resolve(*args):
    """resolve(open, high, low, close, spread, entries, dirs, stop_dists, rrs, max_hold,
    ov_abs, ov_frac[, spike_side, lam]) -> (r, exit_idx, reason)."""
    global _RESOLVE
    if _RESOLVE is None:
        _RESOLVE = _resolver()
    if len(args) == 12:
        args = args + (0, 0.0)
    return _RESOLVE(*args)


def overshoot_for(symbol: str) -> tuple[float, float, int, float]:
    """(absolute overshoot, fraction-of-stop overshoot, spike_side, lam) — the app's
    fill model (backtester/fill_model.py), so research and Backtester price stops alike."""
    from backend.backtester.fill_model import get_overshoot_profile, get_spike_fill
    spike = get_spike_fill(symbol)
    if spike is not None:
        return 0.0, 0.0, int(spike[0]), float(spike[1])
    prof = get_overshoot_profile(symbol)
    return ((float(prof.mean), 0.0) if prof.absolute else (0.0, float(prof.mean))) + (0, 0.0)


# ── candidates ───────────────────────────────────────────────────────────────

@dataclass
class CandSet:
    """All candidates of one strategy + signal setting on one market, with
    outcomes for every (stop_atr, rr). Column-oriented for speed."""
    symbol: str
    strategy: str
    signal_params: dict[str, Any]
    t_entry: np.ndarray            # epoch s of the entry bar open
    i_entry: np.ndarray
    direction: np.ndarray
    feats: dict[str, np.ndarray]
    r: dict[tuple[float, float], np.ndarray] = field(default_factory=dict)
    t_exit: dict[tuple[float, float], np.ndarray] = field(default_factory=dict)


def build_candidates(f: Frame, strategy: str, signal_params: dict[str, Any],
                     stop_atr: Sequence[float] = STOP_ATR, rr: Sequence[float] = RR) -> CandSet:
    d_all = raw_signals(f, strategy, signal_params)
    sig = np.flatnonzero(d_all)
    sig = sig[sig + 1 < f.close.size]
    d = d_all[sig].astype(np.int64)
    ent = sig + 1
    cs = CandSet(f.symbol, strategy, dict(signal_params), f.time[ent].astype(np.int64),
                 ent.astype(np.int64), d, features(f, sig, d))
    ov_abs, ov_frac, spike_side, lam = overshoot_for(f.symbol)
    rrs = np.asarray(rr, dtype=np.float64)
    for s in stop_atr:
        sd = s * f.atr[sig]
        good = np.isfinite(sd) & (sd > 0)
        r_full = np.full((sig.size, rrs.size), np.nan)
        t_full = np.zeros((sig.size, rrs.size), dtype=np.int64)
        if good.any():
            r_, ex, _ = resolve(f.open, f.high, f.low, f.close, f.spread,
                                ent[good].astype(np.int64), d[good], sd[good].astype(np.float64),
                                rrs, MAX_HOLD, ov_abs, ov_frac, spike_side, lam)
            r_full[good] = r_
            t_full[good] = f.time[ex]
        for q, rv in enumerate(rrs):
            cs.r[(float(s), float(rv))] = r_full[:, q]
            cs.t_exit[(float(s), float(rv))] = t_full[:, q]
    return cs


# ── selection: combinations, one position at a time, daily cap ──────────────

def pick(cs: CandSet, exit_key: tuple[float, float], use: Iterable[str] = (),
         side: int = 0, lo: int = 0, hi: int = 2**62, max_per_day: int = 4) -> np.ndarray:
    """Indices of the trades actually taken: gates in `use` all true, the side
    filter, inside [lo, hi), no entry before the previous trade's exit, and no
    more than `max_per_day` entries per UTC day."""
    r = cs.r[exit_key]
    tx = cs.t_exit[exit_key]
    mask = np.isfinite(r) & (cs.t_entry >= lo) & (cs.t_entry < hi)
    for g in use:
        mask &= cs.feats[g]
    if side:
        mask &= cs.direction == side
    out = []
    free_at = -1
    day, count = -1, 0
    for a in np.flatnonzero(mask):
        te = cs.t_entry[a]
        dd = te // 86400
        if dd != day:
            day, count = dd, 0
        if max_per_day and count >= max_per_day:
            continue
        # The engine charges its daily budget when it EMITS a signal; the
        # backtester then discards the signal if a position is still open.
        # So the budget is spent before the one-position rule, not after.
        count += 1
        if te < free_at:
            continue
        out.append(a)
        free_at = tx[a]
    return np.asarray(out, dtype=np.int64)


def stats(rs: np.ndarray) -> dict[str, Any]:
    rs = np.asarray(rs, dtype=float)
    if rs.size == 0:
        return {"n": 0, "avg_r": None, "total_r": 0.0, "win_rate": None, "pf": None, "t": 0.0}
    gw, gl = rs[rs > 0].sum(), -rs[rs < 0].sum()
    sd = float(rs.std(ddof=1)) if rs.size > 2 else 0.0
    return {
        "n": int(rs.size),
        "avg_r": round(float(rs.mean()), 4),
        "total_r": round(float(rs.sum()), 2),
        "win_rate": round(float((rs > 0).mean()), 4),
        "pf": round(float(gw / gl), 3) if gl > 0 else None,
        "t": round(float(rs.mean()) / (sd / math.sqrt(rs.size)), 2) if sd > 0 else 0.0,
    }


def quarter_consistency(cs: CandSet, taken: np.ndarray, exit_key: tuple[float, float],
                        lo: int, hi: int) -> tuple[int, int]:
    """(quarters with positive total R, quarters with any trades) inside [lo, hi)."""
    import datetime as _dt
    r = cs.r[exit_key][taken]
    t = cs.t_entry[taken]
    buckets: dict[tuple[int, int], float] = {}
    for tt, rr in zip(t, r):
        if lo <= tt < hi:
            d = _dt.datetime.utcfromtimestamp(int(tt))
            k = (d.year, (d.month - 1) // 3)
            buckets[k] = buckets.get(k, 0.0) + float(rr)
    return sum(v > 0 for v in buckets.values()), len(buckets)


def combos(names: Sequence[str], max_k: int = 2) -> list[tuple[str, ...]]:
    import itertools
    out: list[tuple[str, ...]] = [()]
    for k in range(1, max_k + 1):
        out += [c for c in itertools.combinations(names, k)
                if not ({"adx_trend", "adx_range"} <= set(c)) and not ({"london", "newyork"} <= set(c))]
    return out
