"""
backend/analytics/htf_regime.py — [P5.2]

Higher-timeframe market state, labelled at the moment a decision was made —
and never a moment later.

WHAT THIS IS FOR
----------------
Phase 5's question is conditioning: are there states of the world in which an
existing signal has a different expectancy? That needs each trade labelled with
the state that was OBSERVABLE when the trade was taken. The labels here are the
pre-registered conditioning variables from the plan (§2.3):

    d1_trend          up / flat / down        close vs EMA20 vs EMA50 on closed D1 bars
    h4_vol            low / normal / high     20-bar realised vol vs its own 60-value median
    session           ASIAN / LONDON / ...    at the decision time
    vwap_distance     inside_1sd / 1_to_2sd / beyond_2sd   from a rolling base-TF VWAP
    profile_position  above_value / in_value / below_value vs the 70% value area

The thresholds were fixed before any study was run. Every extra bucket, every
retuned cut-off, is another hypothesis the study's family-wise control has to
pay for — so they stay put, and a study that wants different ones declares them
up front and accepts the wider bar.

Two plan variables are deliberately absent: event proximity (there is no
historical calendar to label past trades with) and GEX regime (no historical
options data). Labelling those from today's feeds onto last month's trades would
be look-ahead with extra steps.

THE LOOK-AHEAD TRAP THIS MODULE EXISTS TO AVOID
-----------------------------------------------
A D1 bar is timestamped at its OPEN. Using "today's D1 close" for a trade taken
at 10:00 today uses a close that has not happened yet. `last_closed_index`
therefore selects bars whose CLOSE time is at or before the moment the decision
was made, on every timeframe, including the trading one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backend.utils.timeutils import detect_session

VARIABLES: tuple[str, ...] = ("d1_trend", "h4_vol", "session", "vwap_distance", "profile_position")


@dataclass(frozen=True)
class HTFSeries:
    """Bars on one timeframe. `time` is each bar's OPEN, in epoch seconds."""

    time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    period_seconds: int
    volume: np.ndarray | None = None

    @classmethod
    def from_rates(cls, rates: dict[str, Any], period_seconds: int) -> "HTFSeries":
        vol = rates.get("volume")
        return cls(
            time=np.asarray(rates["time"], dtype=np.int64),
            open=np.asarray(rates["open"], dtype=float),
            high=np.asarray(rates["high"], dtype=float),
            low=np.asarray(rates["low"], dtype=float),
            close=np.asarray(rates["close"], dtype=float),
            period_seconds=int(period_seconds),
            volume=np.asarray(vol, dtype=float) if vol is not None else None,
        )

    def last_closed_index(self, available_at: int) -> int | None:
        """Index of the newest bar that had CLOSED by `available_at`, or None."""
        closes = self.time + self.period_seconds
        k = int(np.searchsorted(closes, int(available_at), side="right")) - 1
        return k if k >= 0 else None


def _ema(x: np.ndarray, span: int) -> np.ndarray:
    """Causal EMA: element k uses bars 0..k only."""
    out = np.empty(len(x), dtype=float)
    if len(x) == 0:
        return out
    a = 2.0 / (span + 1.0)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1.0 - a) * out[i - 1]
    return out


@dataclass
class RegimeLabeler:
    """Labels the pre-registered conditioning variables at a decision time."""

    base: HTFSeries
    h4: HTFSeries | None = None
    d1: HTFSeries | None = None
    ema_fast: int = 20
    ema_slow: int = 50
    vol_lookback: int = 20
    vol_median_window: int = 60
    vol_low: float = 0.8
    vol_high: float = 1.25
    vwap_lookback: int = 24
    profile_lookback: int = 120
    profile_bins: int = 24
    value_area_pct: float = 0.70
    _d1_fast: np.ndarray | None = field(default=None, init=False, repr=False)
    _d1_slow: np.ndarray | None = field(default=None, init=False, repr=False)
    _h4_rv: np.ndarray | None = field(default=None, init=False, repr=False)
    _h4_rv_med: np.ndarray | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.d1 is not None:
            self._d1_fast = _ema(self.d1.close, self.ema_fast)
            self._d1_slow = _ema(self.d1.close, self.ema_slow)
        if self.h4 is not None:
            self._h4_rv, self._h4_rv_med = self._vol_arrays(self.h4.close)

    # ── precomputation ───────────────────────────────────────────────────────
    def _vol_arrays(self, close: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n = len(close)
        rv = np.full(n, np.nan)
        med = np.full(n, np.nan)
        if n < 2:
            return rv, med
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.r_[np.nan, np.diff(np.log(close))]
        L, M = self.vol_lookback, self.vol_median_window
        for k in range(L, n):
            w = r[k - L + 1:k + 1]
            if np.all(np.isfinite(w)):
                rv[k] = float(np.std(w, ddof=1))
        for k in range(L + M - 1, n):
            w = rv[k - M + 1:k + 1]
            if np.all(np.isfinite(w)):
                med[k] = float(np.median(w))
        return rv, med

    # ── the labels ───────────────────────────────────────────────────────────
    def d1_trend(self, available_at: int) -> str | None:
        if self.d1 is None:
            return None
        k = self.d1.last_closed_index(available_at)
        # EMAs seeded at the first bar are biased until ~2 slow periods in
        if k is None or k < 2 * self.ema_slow:
            return None
        c, f, s = float(self.d1.close[k]), float(self._d1_fast[k]), float(self._d1_slow[k])
        if c > f > s:
            return "up"
        if c < f < s:
            return "down"
        return "flat"

    def h4_vol(self, available_at: int) -> str | None:
        if self.h4 is None:
            return None
        k = self.h4.last_closed_index(available_at)
        if k is None:
            return None
        rv, med = self._h4_rv[k], self._h4_rv_med[k]
        if not (np.isfinite(rv) and np.isfinite(med)) or med <= 0:
            return None
        ratio = rv / med
        if ratio < self.vol_low:
            return "low"
        if ratio > self.vol_high:
            return "high"
        return "normal"

    def vwap_distance(self, available_at: int) -> str | None:
        kb = self.base.last_closed_index(available_at)
        L = self.vwap_lookback
        if kb is None or kb + 1 < L:
            return None
        w = slice(kb + 1 - L, kb + 1)
        tp = (self.base.high[w] + self.base.low[w] + self.base.close[w]) / 3.0
        v = self.base.volume[w] if self.base.volume is not None else np.ones(L)
        if not np.all(np.isfinite(v)) or v.sum() <= 0:
            v = np.ones(L)
        vwap = float((tp * v).sum() / v.sum())
        sd = math.sqrt(float((v * (tp - vwap) ** 2).sum() / v.sum()))
        if sd <= 0:
            return None
        z = abs(float(self.base.close[kb]) - vwap) / sd
        if z < 1.0:
            return "inside_1sd"
        if z <= 2.0:
            return "1_to_2sd"
        return "beyond_2sd"

    def profile_position(self, available_at: int) -> str | None:
        kb = self.base.last_closed_index(available_at)
        L = self.profile_lookback
        if kb is None or kb + 1 < L:
            return None
        w = slice(kb + 1 - L, kb + 1)
        h, lo_, c = self.base.high[w], self.base.low[w], self.base.close[w]
        v = self.base.volume[w] if self.base.volume is not None else np.ones(L)
        if not np.all(np.isfinite(v)) or v.sum() <= 0:
            v = np.ones(L)
        hi, lo = float(h.max()), float(lo_.min())
        if hi <= lo:
            return None
        bins = self.profile_bins
        width = (hi - lo) / bins
        tp = (h + lo_ + c) / 3.0
        b = np.clip(((tp - lo) / width).astype(int), 0, bins - 1)
        vol = np.bincount(b, weights=v, minlength=bins)
        total = float(vol.sum())
        poc = int(vol.argmax())
        lo_b = hi_b = poc
        acc = float(vol[poc])
        while acc < self.value_area_pct * total and (lo_b > 0 or hi_b < bins - 1):
            down = float(vol[lo_b - 1]) if lo_b > 0 else -1.0
            up = float(vol[hi_b + 1]) if hi_b < bins - 1 else -1.0
            if up >= down:
                hi_b += 1
                acc += float(vol[hi_b])
            else:
                lo_b -= 1
                acc += float(vol[lo_b])
        val, vah = lo + lo_b * width, lo + (hi_b + 1) * width
        last = float(self.base.close[kb])
        if last > vah:
            return "above_value"
        if last < val:
            return "below_value"
        return "in_value"

    def label(self, available_at: int) -> dict[str, str | None]:
        """Every pre-registered variable, as observable at `available_at`."""
        t = int(available_at)
        # `detect_session` returns "UNKNOWN" for the hours between the NY close
        # and the Asian open. That is a real, recurring block of the day, not a
        # data error — named as such so a study table does not read as broken.
        session = detect_session(t)
        return {
            "d1_trend": self.d1_trend(t),
            "h4_vol": self.h4_vol(t),
            "session": "off_hours" if session == "UNKNOWN" else session,
            "vwap_distance": self.vwap_distance(t),
            "profile_position": self.profile_position(t),
        }
