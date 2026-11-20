"""Bar indicators for the scalpers (numpy, closed bars only).

Every value at index i uses bars 0..i - a strategy deciding at the close of
bar i may use index i, never i+1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

NY = ZoneInfo("America/New_York")


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.empty(len(x))
    if not len(x):
        return out
    a = 2.0 / (n + 1)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder's moving average (ATR, ADX, as TradingView/MT5)."""
    out = np.empty(len(x))
    if not len(x):
        return out
    out[0] = x[0]
    a = 1.0 / n
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def true_range(h, l, c):  # noqa: E741
    pc = np.r_[c[0], c[:-1]]
    return np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))


def atr(h, l, c, n: int = 14):  # noqa: E741
    return rma(true_range(h, l, c), n)


def adx(h, l, c, n: int = 14):  # noqa: E741
    up = np.r_[0, h[1:] - h[:-1]]
    dn = np.r_[0, l[:-1] - l[1:]]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = rma(true_range(h, l, c), n)
    tr = np.where(tr == 0, 1e-12, tr)
    pdi = 100 * rma(pdm, n) / tr
    ndi = 100 * rma(ndm, n) / tr
    dx = 100 * abs(pdi - ndi) / np.where(pdi + ndi == 0, 1e-12, pdi + ndi)
    return rma(dx, n)


def roll_max(x: np.ndarray, n: int) -> np.ndarray:
    """Max of the n bars BEFORE i (i excluded); nan for the first n."""
    out = np.full(len(x), np.nan)
    from numpy.lib.stride_tricks import sliding_window_view
    if len(x) > n:
        out[n:] = sliding_window_view(x, n).max(axis=1)[:-1]
    return out


def roll_min(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    from numpy.lib.stride_tricks import sliding_window_view
    if len(x) > n:
        out[n:] = sliding_window_view(x, n).min(axis=1)[:-1]
    return out


def roll_mean(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) > n:
        c = np.cumsum(np.r_[0, x])
        out[n:] = (c[n:-1] - c[:-n - 1]) / n
    return out


def session_vwap(t_s: np.ndarray, h, l, c, vol, session_key) -> np.ndarray:  # noqa: E741
    """VWAP of typical price, reset whenever session_key(t) changes."""
    tp = (h + l + c) / 3
    out = np.empty(len(c))
    keys = np.array([session_key(int(x)) for x in t_s])
    pv = v = 0.0
    prev = None
    for i in range(len(c)):
        if keys[i] != prev:
            pv = v = 0.0
            prev = keys[i]
        pv += tp[i] * vol[i]
        v += vol[i]
        out[i] = pv / v if v else tp[i]
    return out


def ny_minutes(t_s: np.ndarray) -> np.ndarray:
    """Minutes after midnight New York time, per bar start (DST-correct)."""
    out = np.empty(len(t_s), dtype=np.int32)
    cache: dict[int, int] = {}
    for i, t in enumerate(t_s):
        day = int(t) // 86400
        off = cache.get(day)
        if off is None:
            d = datetime.fromtimestamp(day * 86400 + 43200, timezone.utc).astimezone(NY)
            off = int(d.utcoffset().total_seconds())
            cache[day] = off
        out[i] = ((int(t) + off) % 86400) // 60
    return out


def ny_day(t: int) -> int:
    d = datetime.fromtimestamp(int(t), timezone.utc).astimezone(NY)
    return d.year * 10000 + d.month * 100 + d.day


def utc_day(t: int) -> int:
    return int(t) // 86400


def higher_tf(bars_t: np.ndarray, htf_t: np.ndarray, htf_val: np.ndarray, htf_seconds: int) -> np.ndarray:
    """For each lower-TF bar, the value of the last CLOSED higher-TF bar."""
    # a higher-TF bar starting at T is closed from T + htf_seconds
    idx = np.searchsorted(htf_t + htf_seconds, bars_t, side="right") - 1
    out = np.full(len(bars_t), np.nan)
    ok = idx >= 0
    out[ok] = htf_val[idx[ok]]
    return out


def clv(o, h, l, c):  # noqa: E741
    """Where the close sits in the bar's range: 0 = low, 1 = high."""
    rng = np.where(h - l == 0, 1e-12, h - l)
    return (c - l) / rng


def fractal_highs(h: np.ndarray, k: int = 2) -> np.ndarray:
    """Swing high at i (k bars each side). Known only at i+k."""
    out = np.zeros(len(h), bool)
    for i in range(k, len(h) - k):
        w = h[i - k:i + k + 1]
        out[i] = h[i] == w.max() and (w == h[i]).sum() == 1
    return out


def fractal_lows(l: np.ndarray, k: int = 2) -> np.ndarray:  # noqa: E741
    out = np.zeros(len(l), bool)
    for i in range(k, len(l) - k):
        w = l[i - k:i + k + 1]
        out[i] = l[i] == w.min() and (w == l[i]).sum() == 1
    return out
