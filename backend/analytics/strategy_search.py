"""
backend/analytics/strategy_search.py

Which strategy made money on THIS market over the recent months — tested so the
answer holds up when it is traded, not only when it is backtested.

WHAT IT ANSWERS
---------------
For one market (XAUUSD, US Tech 100, US SP 500, ...), for each classic strategy
family, what would it have earned over the most recent months if its settings
had been chosen BEFORE those months started? That last clause is the difference
between a strategy and a curve-fit: every family tries a handful of settings,
and the best-looking setting on the full history is always flattering.

HOW
---
1. Each family has a small, fixed grid of settings.
2. Settings are chosen ONLY on the in-sample window (everything before the
   out-of-sample start).
3. The chosen settings run unchanged through the out-of-sample window — the most
   recent months. That is the number reported and the one to judge.
4. Entry at the NEXT bar's open after the signal bar closes: no look-ahead.
5. The bar's recorded spread is charged on every trade. A bar that opens beyond
   the stop fills at that open; a bar touching both stop and target counts as a
   loss. One position per family per market at a time.
6. Capital adequacy is reported alongside profit: at $350 and 1% risk the budget
   is $3.50 per trade, and on many markets the broker's minimum lot risks more
   than that on a sensible stop. A strategy the account cannot size is not
   tradable on that account, however good its curve.

THE FAMILIES
------------
Chosen because each has decades of published evidence behind it across asset
classes — not because they looked good here first.

  donchian      trend breakout of the prior N-bar high/low, ATR stop, trailing or channel exit
  ema_pullback  trade with the EMA regime on a pullback to the fast EMA, fixed reward:risk
  rsi2          short-term mean reversion (Connors): RSI(2) extreme in the direction of the 200-bar trend
  bollinger     fade a close back inside the 2-sigma band, exit at the middle band
  orb           opening-range breakout of the London or New York session (M15)
  tsmom         daily time-series momentum: hold the sign of the N-day return (D1)
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import numpy as np
import pandas as pd
import pytz

TF_SECONDS = {"M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}


# ── data ─────────────────────────────────────────────────────────────────────

@dataclass
class Bars:
    symbol: str
    timeframe: str
    time: np.ndarray       # epoch seconds, bar OPEN
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    spread: np.ndarray     # price units, per bar
    volume: np.ndarray | None = None   # tick volume — the only order-flow history MT5 keeps

    @classmethod
    def from_rates(cls, symbol: str, timeframe: str, rates: Any, point: float) -> "Bars":
        names = rates.dtype.names
        sp = np.asarray(rates["spread"], dtype=float) * float(point) if "spread" in names \
            else np.zeros(len(rates))
        vol = np.asarray(rates["tick_volume"], dtype=float) if "tick_volume" in names else None
        return cls(symbol, timeframe,
                   np.asarray(rates["time"], dtype=np.int64),
                   np.asarray(rates["open"], dtype=float),
                   np.asarray(rates["high"], dtype=float),
                   np.asarray(rates["low"], dtype=float),
                   np.asarray(rates["close"], dtype=float),
                   np.maximum(sp, 0.0), vol)

    def __len__(self) -> int:
        return len(self.close)


# ── indicators (all causal: element i uses bars 0..i only) ────────────────────

def atr(b: Bars, n: int = 14) -> np.ndarray:
    pc = np.r_[b.close[0], b.close[:-1]]
    tr = np.maximum(b.high - b.low, np.maximum(np.abs(b.high - pc), np.abs(b.low - pc)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n).mean().to_numpy()


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n).std(ddof=0).to_numpy()


def rsi(x: np.ndarray, n: int) -> np.ndarray:
    d = np.diff(x, prepend=x[0])
    up = pd.Series(np.clip(d, 0, None)).ewm(alpha=1.0 / n, adjust=False).mean()
    dn = pd.Series(np.clip(-d, 0, None)).ewm(alpha=1.0 / n, adjust=False).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = up / dn
        out = 100.0 - 100.0 / (1.0 + rs)
    return np.where(dn.to_numpy() == 0, 100.0, out.to_numpy())


def prior_max(x: np.ndarray, n: int) -> np.ndarray:
    """max of the n bars BEFORE i — excludes bar i itself."""
    return pd.Series(x).rolling(n).max().shift(1).to_numpy()


def prior_min(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n).min().shift(1).to_numpy()


# ── the simulator ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Signal:
    i: int                      # bar whose CLOSE produced the signal
    direction: int              # +1 long, -1 short
    stop_dist: float            # price units from entry
    target_rr: float | None = None
    exit: str = "none"          # none | trail_atr | sma | channel | flip
    exit_param: float | None = None
    max_hold: int = 500
    session_end: int | None = None   # epoch seconds: flatten at the first bar reaching it


@dataclass
class Trade:
    i_signal: int
    i_entry: int
    i_exit: int
    t_entry: int
    t_exit: int
    direction: int
    entry: float
    exit: float
    stop_dist: float
    r: float
    reason: str


def simulate(b: Bars, signals: list[Signal], aux: dict[str, np.ndarray]) -> list[Trade]:
    n = len(b)
    tf = TF_SECONDS.get(b.timeframe, 3600)
    trades: list[Trade] = []
    last_exit = -1
    for s in sorted(signals, key=lambda s: s.i):
        i = s.i
        if i <= last_exit or i + 1 >= n or not (s.stop_dist > 0 and math.isfinite(s.stop_dist)):
            continue
        e, d = i + 1, s.direction
        entry = float(b.open[e])
        stop = entry - d * s.stop_dist
        target = entry + d * s.target_rr * s.stop_dist if s.target_rr else None
        end = min(n - 1, e + s.max_hold)
        exit_px, reason, j = None, "", e
        while j <= end:
            o, h, lo, c = float(b.open[j]), float(b.high[j]), float(b.low[j]), float(b.close[j])
            if s.exit == "trail_atr" and j > e:
                a = aux["atr"][j - 1]
                if np.isfinite(a):
                    cand = float(b.close[j - 1]) - d * float(s.exit_param) * float(a)
                    stop = max(stop, cand) if d > 0 else min(stop, cand)
            if j > e:
                if (d > 0 and o <= stop) or (d < 0 and o >= stop):
                    exit_px, reason = o, "STOP"
                    break
                if target is not None and ((d > 0 and o >= target) or (d < 0 and o <= target)):
                    exit_px, reason = o, "TARGET"
                    break
            if (d > 0 and lo <= stop) or (d < 0 and h >= stop):      # checked first: ties lose
                exit_px, reason = stop, "STOP"
                break
            if target is not None and ((d > 0 and h >= target) or (d < 0 and lo <= target)):
                exit_px, reason = target, "TARGET"
                break
            if s.session_end is not None and int(b.time[j]) + tf >= s.session_end:
                exit_px, reason = c, "SESSION_END"
                break
            if s.exit == "sma":
                m = aux["sma_exit"][j]
                if np.isfinite(m) and ((d > 0 and c >= m) or (d < 0 and c <= m)):
                    exit_px, reason = c, "SMA"
                    break
            if s.exit == "channel":
                lo_c, hi_c = aux["chan_lo"][j], aux["chan_hi"][j]
                if (d > 0 and np.isfinite(lo_c) and c < lo_c) or (d < 0 and np.isfinite(hi_c) and c > hi_c):
                    exit_px, reason = c, "CHANNEL"
                    break
            if s.exit == "flip" and aux["sign"][j] != d:
                exit_px, reason = c, "FLIP"
                break
            j += 1
        if exit_px is None:
            j = end
            exit_px, reason = float(b.close[j]), "TIME"
        r = d * (exit_px - entry) / s.stop_dist - float(b.spread[e]) / s.stop_dist
        trades.append(Trade(i, e, j, int(b.time[e]), int(b.time[j]), d, entry, float(exit_px),
                            float(s.stop_dist), float(r), reason))
        last_exit = j
    return trades


# ── the families ─────────────────────────────────────────────────────────────

def _sides(side: str) -> tuple[bool, bool]:
    return True, side == "both"


def _donchian(b: Bars, p: dict) -> tuple[list[Signal], dict]:
    N, k = p["n"], p["k"]
    a = atr(b, 14)
    hi, lo = prior_max(b.high, N), prior_min(b.low, N)
    half = max(5, N // 2)
    aux = {"atr": a, "chan_lo": prior_min(b.low, half), "chan_hi": prior_max(b.high, half)}
    want_long, want_short = _sides(p["side"])
    exit_kind = "trail_atr" if p["exit"] == "trail" else "channel"
    sig = []
    for i in range(N + 1, len(b) - 1):
        if not (np.isfinite(a[i]) and a[i] > 0 and np.isfinite(hi[i]) and np.isfinite(hi[i - 1])):
            continue
        c, cp = b.close[i], b.close[i - 1]
        if want_long and c > hi[i] and cp <= hi[i - 1]:
            sig.append(Signal(i, 1, k * a[i], exit=exit_kind, exit_param=3.0, max_hold=1000))
        elif want_short and c < lo[i] and cp >= lo[i - 1]:
            sig.append(Signal(i, -1, k * a[i], exit=exit_kind, exit_param=3.0, max_hold=1000))
    return sig, aux


def _ema_pullback(b: Bars, p: dict) -> tuple[list[Signal], dict]:
    f, s = p["emas"]
    ef, es, a = ema(b.close, f), ema(b.close, s), atr(b, 14)
    want_long, want_short = _sides(p["side"])
    sig = []
    for i in range(s + 1, len(b) - 1):
        if not (np.isfinite(a[i]) and a[i] > 0):
            continue
        if want_long and ef[i] > es[i] and b.low[i] <= ef[i] < b.close[i]:
            sig.append(Signal(i, 1, 2.0 * a[i], target_rr=p["rr"], max_hold=200))
        elif want_short and ef[i] < es[i] and b.high[i] >= ef[i] > b.close[i]:
            sig.append(Signal(i, -1, 2.0 * a[i], target_rr=p["rr"], max_hold=200))
    return sig, {"atr": a}


def _rsi2(b: Bars, p: dict) -> tuple[list[Signal], dict]:
    r2, s200, s5, a = rsi(b.close, 2), sma(b.close, 200), sma(b.close, 5), atr(b, 14)
    th = p["th"]
    want_long, want_short = _sides(p["side"])
    sig = []
    for i in range(201, len(b) - 1):
        if not (np.isfinite(a[i]) and a[i] > 0 and np.isfinite(s200[i])):
            continue
        if want_long and r2[i] < th and b.close[i] > s200[i]:
            sig.append(Signal(i, 1, 3.0 * a[i], exit="sma", max_hold=p["hold"]))
        elif want_short and r2[i] > 100 - th and b.close[i] < s200[i]:
            sig.append(Signal(i, -1, 3.0 * a[i], exit="sma", max_hold=p["hold"]))
    return sig, {"atr": a, "sma_exit": s5}


def _bollinger(b: Bars, p: dict) -> tuple[list[Signal], dict]:
    m, sd, a = sma(b.close, 20), rolling_std(b.close, 20), atr(b, 14)
    k = p["k"]
    up, dn = m + k * sd, m - k * sd
    want_long, want_short = _sides(p["side"])
    sig = []
    for i in range(21, len(b) - 1):
        if not (np.isfinite(a[i]) and a[i] > 0 and np.isfinite(dn[i - 1])):
            continue
        c, cp = b.close[i], b.close[i - 1]
        if want_long and cp < dn[i - 1] and c > dn[i] and c < m[i]:
            stop_px = min(b.low[i - 1], b.low[i]) - 0.5 * a[i]
            sig.append(Signal(i, 1, c - stop_px, exit="sma", max_hold=100))
        elif want_short and cp > up[i - 1] and c < up[i] and c > m[i]:
            stop_px = max(b.high[i - 1], b.high[i]) + 0.5 * a[i]
            sig.append(Signal(i, -1, stop_px - c, exit="sma", max_hold=100))
    return sig, {"atr": a, "sma_exit": m}


_SESSIONS = {
    "london": ("Europe/London", (8, 0), (16, 30)),
    "ny": ("America/New_York", (9, 30), (16, 0)),
}


def _orb(b: Bars, p: dict) -> tuple[list[Signal], dict]:
    """Opening-range breakout. Session opens are DST-aware: London opens 08:00
    local and New York 09:30 local, which is a different UTC hour in summer and
    winter — a fixed UTC hour would be an hour wrong for half the year."""
    tzname, (oh, om), (ch, cm) = _SESSIONS[p["session"]]
    tz = pytz.timezone(tzname)
    a = atr(b, 14)
    rng_s = p["range_min"] * 60
    want_long, want_short = _sides(p["side"])
    sig = []
    days = sorted({datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(tz).date() for t in b.time})
    for day in days:
        open_ts = int(tz.localize(datetime(day.year, day.month, day.day, oh, om)).timestamp())
        close_ts = int(tz.localize(datetime(day.year, day.month, day.day, ch, cm)).timestamp())
        r0 = int(np.searchsorted(b.time, open_ts, side="left"))
        r1 = int(np.searchsorted(b.time, open_ts + rng_s, side="left"))
        if r0 >= len(b) or r1 - r0 < max(1, p["range_min"] // 15) or int(b.time[r0]) != open_ts:
            continue  # the opening range is incomplete (holiday, data gap)
        rh, rl = float(b.high[r0:r1].max()), float(b.low[r0:r1].min())
        if rh <= rl:
            continue
        w_end = int(np.searchsorted(b.time, open_ts + rng_s + 3 * 3600, side="left"))
        for i in range(r1, min(w_end, len(b) - 1)):
            c = float(b.close[i])
            ai = a[i] if np.isfinite(a[i]) else 0.0
            tr = None if p["exit"] == "session" else float(p["exit"])
            if want_long and c > rh:
                sig.append(Signal(i, 1, max(c - rl, 0.25 * ai), target_rr=tr, session_end=close_ts, max_hold=100))
                break
            if want_short and c < rl:
                sig.append(Signal(i, -1, max(rh - c, 0.25 * ai), target_rr=tr, session_end=close_ts, max_hold=100))
                break
    return sig, {"atr": a}


def _session_bounds(b: Bars, session: str) -> list[tuple[int, int, int]]:
    """(open_ts, close_ts, first_bar_index) per trading day, DST-aware."""
    tzname, (oh, om), (ch, cm) = _SESSIONS[session]
    tz = pytz.timezone(tzname)
    days = sorted({datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(tz).date() for t in b.time})
    out = []
    for day in days:
        o = int(tz.localize(datetime(day.year, day.month, day.day, oh, om)).timestamp())
        c = int(tz.localize(datetime(day.year, day.month, day.day, ch, cm)).timestamp())
        r0 = int(np.searchsorted(b.time, o, side="left"))
        if r0 < len(b) and int(b.time[r0]) == o:
            out.append((o, c, r0))
    return out


def _vwap(b: Bars, p: dict) -> tuple[list[Signal], dict]:
    """Session-anchored VWAP pullback. Weighted by tick volume — MT5 keeps no
    real traded volume for CFDs. Trades only WITH the session's VWAP slope:
    long when price holds above a rising VWAP and a bar dips to it and closes back
    above; mirror for shorts."""
    if b.volume is None:
        return [], {}
    a = atr(b, 14)
    tp = (b.high + b.low + b.close) / 3.0
    want_long, want_short = _sides(p["side"])
    tr = None if p["exit"] == "session" else float(p["exit"])
    sig = []
    for open_ts, close_ts, r0 in _session_bounds(b, p["session"]):
        r_end = int(np.searchsorted(b.time, close_ts - 3600, side="left"))
        if r_end - r0 < 8:
            continue
        v = np.maximum(b.volume[r0:r_end], 1.0)
        vw = np.cumsum(tp[r0:r_end] * v) / np.cumsum(v)
        for k in range(4, r_end - r0):      # first hour builds the VWAP
            i = r0 + k
            ai = a[i]
            if not (np.isfinite(ai) and ai > 0) or i + 1 >= len(b):
                continue
            slope = vw[k] - vw[k - 4]
            c, lo, hi = float(b.close[i]), float(b.low[i]), float(b.high[i])
            if want_long and slope > 0 and lo <= vw[k] < c:
                sig.append(Signal(i, 1, max(c - lo + 0.25 * ai, 0.5 * ai), target_rr=tr,
                                  session_end=close_ts, max_hold=64))
            elif want_short and slope < 0 and hi >= vw[k] > c:
                sig.append(Signal(i, -1, max(hi - c + 0.25 * ai, 0.5 * ai), target_rr=tr,
                                  session_end=close_ts, max_hold=64))
    return sig, {"atr": a}


def _vol_breakout(b: Bars, p: dict) -> tuple[list[Signal], dict]:
    """Donchian breakout that needs participation: the breakout bar's tick volume
    must be at least `m` times its 20-bar average (an order-flow proxy)."""
    if b.volume is None:
        return [], {}
    N, m = p["n"], p["m"]
    a = atr(b, 14)
    hi, lo = prior_max(b.high, N), prior_min(b.low, N)
    vavg = pd.Series(b.volume).rolling(20).mean().shift(1).to_numpy()
    want_long, want_short = _sides(p["side"])
    sig = []
    for i in range(N + 1, len(b) - 1):
        if not (np.isfinite(a[i]) and a[i] > 0 and np.isfinite(hi[i]) and np.isfinite(vavg[i]) and vavg[i] > 0):
            continue
        if b.volume[i] < m * vavg[i]:
            continue
        c = b.close[i]
        if want_long and c > hi[i]:
            sig.append(Signal(i, 1, 2.0 * a[i], exit="trail_atr", exit_param=3.0, max_hold=1000))
        elif want_short and c < lo[i]:
            sig.append(Signal(i, -1, 2.0 * a[i], exit="trail_atr", exit_param=3.0, max_hold=1000))
    return sig, {"atr": a}


def _tsmom(b: Bars, p: dict) -> tuple[list[Signal], dict]:
    L = p["lookback"]
    a = atr(b, 20)
    c = b.close
    sign = np.zeros(len(b), dtype=int)
    sign[L:] = np.sign(c[L:] / c[:-L] - 1.0).astype(int)
    want_long, want_short = _sides(p["side"])
    if not want_short:
        sign = np.where(sign < 0, 0, sign)
    sig = []
    for i in range(L + 1, len(b) - 1):
        if sign[i] != 0 and sign[i] != sign[i - 1] and np.isfinite(a[i]) and a[i] > 0:
            sig.append(Signal(i, int(sign[i]), 3.0 * a[i], exit="flip", max_hold=400))
    return sig, {"atr": a, "sign": sign}


def _grid(**axes: list) -> list[dict]:
    keys = list(axes)
    out = [{}]
    for k in keys:
        out = [dict(g, **{k: v}) for g in out for v in axes[k]]
    return out


@dataclass(frozen=True)
class Family:
    name: str
    timeframe: str
    description: str
    grid: tuple
    build: Callable[[Bars, dict], tuple[list[Signal], dict]]
    min_is_trades: int


FAMILIES: tuple[Family, ...] = (
    Family("donchian", "H1", "trend breakout, ATR stop, trailing or channel exit",
           tuple(_grid(n=[20, 55], k=[2.0, 3.0], exit=["trail", "channel"], side=["both", "long"])),
           _donchian, 30),
    Family("ema_pullback", "H1", "trend pullback to the fast EMA, fixed reward:risk",
           tuple(_grid(emas=[(20, 50), (50, 200)], rr=[2.0, 3.0, 5.0, 7.0, 10.0], side=["both", "long"])),
           _ema_pullback, 30),
    Family("vwap", "M15", "session VWAP pullback with the VWAP slope (tick-volume weighted)",
           tuple(_grid(session=["london", "ny"], exit=[1.5, 2.0, 3.0, "session"], side=["both", "long"])),
           _vwap, 30),
    Family("vol_breakout", "H1", "breakout confirmed by a tick-volume surge (order-flow proxy)",
           tuple(_grid(n=[20, 55], m=[1.5, 2.0], side=["both", "long"])),
           _vol_breakout, 30),
    Family("rsi2", "H1", "RSI(2) mean reversion with the 200-bar trend",
           tuple(_grid(th=[5, 10], hold=[24, 48], side=["both", "long"])),
           _rsi2, 30),
    Family("bollinger", "H1", "fade back inside the band, exit at the mean",
           tuple(_grid(k=[2.0, 2.5], side=["both", "long"])),
           _bollinger, 30),
    Family("orb", "M15", "London / New York opening-range breakout",
           tuple(_grid(session=["london", "ny"], range_min=[30, 60], exit=[1.5, 2.0, 3.0, "session"],
                       side=["both"])),
           _orb, 30),
    Family("tsmom", "D1", "daily time-series momentum",
           tuple(_grid(lookback=[20, 60, 120], side=["both", "long"])),
           _tsmom, 8),
)


# ── scoring ──────────────────────────────────────────────────────────────────

def summarize(trades: list[Trade], *, risk_pct: float = 1.0, start_balance: float = 350.0) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_r": None, "total_r": 0.0, "profit_factor": None,
                "max_dd_r": 0.0, "months": 0, "positive_months": 0, "worst_month_r": None,
                "final_balance": start_balance, "return_pct": 0.0, "max_dd_pct": 0.0}
    rs = [t.r for t in trades]
    gw = sum(r for r in rs if r > 0)
    gl = -sum(r for r in rs if r < 0)
    cum = np.cumsum(rs)
    peak = np.maximum.accumulate(np.r_[0.0, cum])[1:]
    monthly: dict[str, float] = {}
    for t in trades:
        k = datetime.fromtimestamp(t.t_exit, tz=timezone.utc).strftime("%Y-%m")
        monthly[k] = monthly.get(k, 0.0) + t.r
    bal, pk, mdd = start_balance, start_balance, 0.0
    for r in rs:
        bal *= max(0.0, 1.0 + risk_pct / 100.0 * r)
        pk = max(pk, bal)
        mdd = max(mdd, (pk - bal) / pk if pk > 0 else 0.0)
    return {
        "n": n,
        "win_rate": round(sum(r > 0 for r in rs) / n, 4),
        "avg_r": round(statistics.mean(rs), 4),
        "total_r": round(float(cum[-1]), 3),
        "profit_factor": round(gw / gl, 3) if gl > 0 else None,
        "max_dd_r": round(float((peak - cum).max()), 3),
        "months": len(monthly),
        "positive_months": sum(v > 0 for v in monthly.values()),
        "worst_month_r": round(min(monthly.values()), 3),
        "final_balance": round(bal, 2),
        "return_pct": round((bal / start_balance - 1.0) * 100.0, 2),
        "max_dd_pct": round(mdd * 100.0, 2),
    }


@dataclass(frozen=True)
class SizingSpec:
    """What one price unit is worth per lot, and the smallest tradable lot."""
    value_per_price_per_lot: float
    min_lot: float


def capital_adequacy(trades: list[Trade], spec: SizingSpec | None, *,
                     balance: float = 350.0, risk_pct: float = 1.0) -> dict[str, Any]:
    if not trades or spec is None or spec.value_per_price_per_lot <= 0:
        return {"expressible_pct": None, "median_risk_pct_at_min_lot": None}
    budget = balance * risk_pct / 100.0
    at_min = [t.stop_dist * spec.value_per_price_per_lot * spec.min_lot for t in trades]
    return {
        "risk_budget_usd": round(budget, 2),
        "expressible_pct": round(100.0 * sum(x <= budget for x in at_min) / len(at_min), 1),
        "median_risk_usd_at_min_lot": round(statistics.median(at_min), 2),
        "median_risk_pct_at_min_lot": round(100.0 * statistics.median(at_min) / balance, 2),
    }


def evaluate_family(b: Bars, fam: Family, *, oos_start: int,
                    spec: SizingSpec | None = None) -> dict[str, Any]:
    """Pick settings on in-sample only; report them unchanged out of sample."""
    from backend.analytics.significance import assess

    rows = []
    for p in fam.grid:
        sigs, aux = fam.build(b, p)
        trades = simulate(b, sigs, aux)
        is_tr = [t for t in trades if t.t_exit < oos_start]
        oos_tr = [t for t in trades if t.t_entry >= oos_start]
        rows.append((p, is_tr, oos_tr))

    def score(row) -> float:
        s = summarize(row[1])
        return s["avg_r"] * math.sqrt(s["n"]) if s["n"] and s["avg_r"] is not None else -math.inf

    eligible = [r for r in rows if len(r[1]) >= fam.min_is_trades and summarize(r[1])["avg_r"] > 0]
    oos_avgs = [summarize(r[2])["avg_r"] for r in rows if r[2]]
    out: dict[str, Any] = {
        "family": fam.name, "timeframe": fam.timeframe, "description": fam.description,
        "configs_tried": len(rows),
        "configs_profitable_in_sample": len(eligible),
        "family_median_oos_avg_r": round(statistics.median(oos_avgs), 4) if oos_avgs else None,
    }
    if not eligible:
        out.update({"chosen": None, "verdict": "NO SETTING WAS PROFITABLE IN-SAMPLE"})
        return out
    best = max(eligible, key=score)
    oos = summarize(best[2])
    sig = assess([t.r for t in best[2]], n_trials=1).to_dict() if len(best[2]) >= 2 else {"verdict": "INSUFFICIENT"}
    out.update({
        "chosen": best[0],
        "in_sample": summarize(best[1]),
        "out_of_sample": oos,
        "oos_significance": sig.get("verdict"),
        "capital_adequacy": capital_adequacy(best[2], spec),
    })
    if oos["n"] == 0:
        out["verdict"] = "NO OUT-OF-SAMPLE TRADES"
    elif oos["avg_r"] > 0 and oos["months"] and oos["positive_months"] / oos["months"] >= 0.5:
        out["verdict"] = "PROFITABLE OUT OF SAMPLE"
    elif oos["avg_r"] > 0:
        out["verdict"] = "PROFITABLE BUT LUMPY"
    else:
        out["verdict"] = "LOST MONEY OUT OF SAMPLE"
    return out
