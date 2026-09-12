"""
backend/analytics/orb_research.py

Opening-range breakout, taken apart: which entry, which exit, which confluence.

For every session and range length it records each session's first breakout as
a SETUP carrying:

  * FEATURES known at the signal bar's close (never later) — the candidate
    confluences: higher-timeframe trend, prior-day high/low, breakout candle
    strength, tick-volume surge (the only order-flow history MT5 keeps), range
    width, timing, Asian range, opening participation, overnight gap;
  * OUTCOMES in R for every exit variant — target 1R..4R, with and without the
    session close, with and without a break-even move at +1R;
  * the outcome of a PYRAMID ADD: one more unit bought (sold) at +1R with its
    stop at the original entry and the same target.

Two entry modes share the same breakout: "break" (market at the next bar's
open, as ORB_v1 trades) and "retest" (a limit at the broken range edge, valid
until the breakout window ends, stop at the far side of the range).

Conservative bar rules throughout: a bar that touches both stop and target is a
loss; a bar that opens beyond a stop fills at the open; an add that fills on a
bar which also trades back through its stop is counted as stopped.

Selection is walk-forward: configurations and filters are chosen on the
in-sample window only and reported unchanged on the later window and on the
earlier holdout.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np
import pytz

from backend.analytics.strategy_search import Bars, atr

SESSIONS = {
    "london": ("Europe/London", (8, 0), (16, 30)),
    "ny": ("America/New_York", (9, 30), (16, 0)),
}
TF = 900
WINDOW_MIN = 180
MIN_STOP_ATR = 0.25
MAX_HOLD_BARS = 96

EXIT_VARIANTS: tuple[tuple[float, bool, bool], ...] = tuple(
    [(rr, sc, False) for rr in (1.0, 1.5, 2.0, 3.0, 4.0) for sc in (True, False)]
    + [(2.0, True, True), (3.0, True, True)]
)
FEATURES = ("trend_align", "prevday_align", "beyond_pdhl", "strong_body", "vol_surge",
            "range_narrow", "early_break", "beyond_asia", "open_vol_high", "gap_small")


def variant_key(rr: float, session_close: bool, breakeven: bool) -> str:
    return f"1:{rr:g}{'' if session_close else ' hold'}{' BE' if breakeven else ''}"


@dataclass
class Setup:
    symbol: str
    session: str
    range_min: int
    entry_mode: str
    day: str
    direction: int
    i_entry: int
    t_entry: int
    entry: float
    stop_dist: float
    close_ts: int
    features: dict[str, bool]
    outcomes: dict[str, tuple] = field(default_factory=dict)
    # key -> (r, t_exit, add_r | None, t_add | None, t_add_exit | None)


def _local_bounds(tz, day, hm_open, hm_close) -> tuple[int, int]:
    o = int(tz.localize(datetime(day.year, day.month, day.day, *hm_open)).timestamp())
    c = int(tz.localize(datetime(day.year, day.month, day.day, *hm_close)).timestamp())
    return o, c


class _Daily:
    """Completed UTC-day OHLCV built from the M15 bars, looked up strictly before a time."""

    def __init__(self, b: Bars):
        day = b.time // 86400
        self.days, starts = np.unique(day, return_index=True)
        ends = np.r_[starts[1:], len(day)]
        self.o = b.open[starts]
        self.c = b.close[ends - 1]
        self.h = np.array([b.high[s:e].max() for s, e in zip(starts, ends)])
        self.l = np.array([b.low[s:e].min() for s, e in zip(starts, ends)])
        pc = np.r_[self.c[0], self.c[:-1]]
        tr = np.maximum(self.h - self.l, np.maximum(np.abs(self.h - pc), np.abs(self.l - pc)))
        self.atr = np.array([tr[max(0, k - 13): k + 1].mean() for k in range(len(tr))])
        self.sma20 = np.array([self.c[max(0, k - 19): k + 1].mean() for k in range(len(self.c))])

    def before(self, t: int) -> int | None:
        k = int(np.searchsorted(self.days, int(t) // 86400, side="left")) - 1
        return k if k >= 20 else None


def build_setups(b: Bars, session: str, range_min: int) -> list[Setup]:
    tzname, hm_o, hm_c = SESSIONS[session]
    tz = pytz.timezone(tzname)
    uk = pytz.timezone("Europe/London")
    a = atr(b, 14)
    daily = _Daily(b)
    vol = b.volume if b.volume is not None else np.ones(len(b))
    days = sorted({datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(tz).date() for t in b.time})
    widths_rel: list[float] = []
    open_vols: list[float] = []
    out: list[Setup] = []
    n = len(b)
    for day in days:
        open_ts, close_ts = _local_bounds(tz, day, hm_o, hm_c)
        r0 = int(np.searchsorted(b.time, open_ts, side="left"))
        r1 = int(np.searchsorted(b.time, open_ts + range_min * 60, side="left"))
        if r0 >= n or int(b.time[r0]) != open_ts or r1 - r0 < max(1, range_min // 15):
            continue
        rh, rl = float(b.high[r0:r1].max()), float(b.low[r0:r1].min())
        if rh <= rl:
            continue
        k = daily.before(open_ts)
        d_atr = float(daily.atr[k]) if k is not None else float("nan")
        w_rel = (rh - rl) / d_atr if k is not None and d_atr > 0 else float("nan")
        o_vol = float(vol[r0:r1].sum())
        narrow = (bool(w_rel < statistics.median(widths_rel[-20:])) if len(widths_rel) >= 10 and math.isfinite(w_rel)
                  else False)
        vol_high = bool(o_vol >= statistics.median(open_vols[-20:])) if len(open_vols) >= 10 else False
        if math.isfinite(w_rel):
            widths_rel.append(w_rel)
        open_vols.append(o_vol)

        w_end = int(np.searchsorted(b.time, open_ts + range_min * 60 + WINDOW_MIN * 60, side="left"))
        i = None
        for j in range(r1, min(w_end, n - 1)):
            if b.close[j] > rh or b.close[j] < rl:
                i = j
                break
        if i is None or k is None:
            continue
        dirn = 1 if b.close[i] > rh else -1
        c = float(b.close[i])
        ai = float(a[i]) if np.isfinite(a[i]) else 0.0

        a_o, a_c = _local_bounds(uk, datetime.fromtimestamp(open_ts, tz=timezone.utc).astimezone(uk).date(), (0, 0), (7, 0))
        a0, a1 = int(np.searchsorted(b.time, a_o, side="left")), int(np.searchsorted(b.time, a_c, side="left"))
        asia_ok = a1 > a0
        bar_rng = float(b.high[i] - b.low[i])
        feats = {
            "trend_align": bool(dirn * (c - daily.sma20[k]) > 0),
            "prevday_align": bool(dirn * (daily.c[k] - daily.o[k]) > 0),
            "beyond_pdhl": bool(c > daily.h[k]) if dirn > 0 else bool(c < daily.l[k]),
            "strong_body": bool(bar_rng > 0 and abs(b.close[i] - b.open[i]) / bar_rng >= 0.5),
            "vol_surge": bool(vol[i] >= 1.5 * vol[r0:r1].mean()),
            "range_narrow": narrow,
            "early_break": bool(i - r1 < 4),
            "beyond_asia": bool(asia_ok and (c > b.high[a0:a1].max() if dirn > 0 else c < b.low[a0:a1].min())),
            "open_vol_high": vol_high,
            "gap_small": bool(d_atr > 0 and abs(b.open[r0] - daily.c[k]) <= 0.25 * d_atr),
        }
        day_s = str(day)

        # break: market at the next bar's open, stop re-anchored at the same distance
        stop = max(c - rl, MIN_STOP_ATR * ai) if dirn > 0 else max(rh - c, MIN_STOP_ATR * ai)
        if stop > 0 and i + 1 < n:
            s = Setup(b.symbol, session, range_min, "break", day_s, dirn, i + 1, int(b.time[i + 1]),
                      float(b.open[i + 1]), float(stop), close_ts, feats)
            _resolve_all(b, s, gap_check_on_entry_bar=False)
            out.append(s)

        # retest: limit at the broken edge until the window ends, stop beyond the far side
        edge, far = (rh, rl) if dirn > 0 else (rl, rh)
        sd = max(abs(edge - far), MIN_STOP_ATR * ai)
        for j in range(i + 1, min(w_end, n)):
            if (dirn > 0 and b.low[j] <= edge) or (dirn < 0 and b.high[j] >= edge):
                fill = min(float(b.open[j]), edge) if dirn > 0 else max(float(b.open[j]), edge)
                s = Setup(b.symbol, session, range_min, "retest", day_s, dirn, j, int(b.time[j]),
                          fill, float(sd), close_ts, feats)
                _resolve_all(b, s, gap_check_on_entry_bar=False)
                out.append(s)
                break
    return out


class _Lists:
    """The bar arrays as Python lists. The exit walks index single elements
    millions of times, and a numpy scalar read costs several times a list read."""

    def __init__(self, b: Bars):
        self.open, self.high, self.low = b.open.tolist(), b.high.tolist(), b.low.tolist()
        self.close, self.time, self.spread = b.close.tolist(), b.time.tolist(), b.spread.tolist()


_LIST_CACHE: dict[int, tuple[Bars, _Lists]] = {}


def _lists(b: Bars) -> _Lists:
    hit = _LIST_CACHE.get(id(b))
    if hit is None or hit[0] is not b:
        _LIST_CACHE.clear()
        hit = _LIST_CACHE[id(b)] = (b, _Lists(b))
    return hit[1]


def _resolve_all(b: Bars, s: Setup, gap_check_on_entry_bar: bool) -> None:
    bl = _lists(b)
    for rr, sc, be in EXIT_VARIANTS:
        s.outcomes[variant_key(rr, sc, be)] = _resolve(bl, s, rr, sc, be)


def _resolve(b, s: Setup, rr: float, session_close: bool, breakeven: bool) -> tuple:
    n, d, e, entry, sd = len(b.close), s.direction, s.i_entry, s.entry, s.stop_dist
    stop, target = entry - d * sd, entry + d * rr * sd
    add_level, add_stop = entry + d * sd, entry
    can_add = rr > 1.0
    add_state, add_fill_bar, add_exit, add_exit_t = None, None, None, None   # None | "open" | "closed"
    end = min(n - 1, e + MAX_HOLD_BARS)
    be_armed = False
    exit_px, j = None, e
    while j <= end:
        o, h, lo, c = float(b.open[j]), float(b.high[j]), float(b.low[j]), float(b.close[j])
        if be_armed:
            stop = entry
        if j > e:
            if (d > 0 and o <= stop) or (d < 0 and o >= stop):
                exit_px = o
            elif (d > 0 and o >= target) or (d < 0 and o <= target):
                exit_px = o
            if add_state == "open" and (exit_px is not None or (d > 0 and o <= add_stop) or (d < 0 and o >= add_stop)):
                add_state, add_exit, add_exit_t = "closed", o, int(b.time[j])
            if exit_px is not None:
                break
        if add_state == "open" and ((d > 0 and lo <= add_stop) or (d < 0 and h >= add_stop)):
            add_state, add_exit, add_exit_t = "closed", add_stop, int(b.time[j])
        if can_add and add_state is None and ((d > 0 and h >= add_level) or (d < 0 and lo <= add_level)):
            add_state, add_fill_bar = "open", j
            if (d > 0 and lo <= add_stop) or (d < 0 and h >= add_stop):
                add_state, add_exit, add_exit_t = "closed", add_stop, int(b.time[j])
        if (d > 0 and lo <= stop) or (d < 0 and h >= stop):
            exit_px = stop
            if add_state == "open":
                add_state, add_exit, add_exit_t = "closed", add_stop, int(b.time[j])
            break
        if (d > 0 and h >= target) or (d < 0 and lo <= target):
            exit_px = target
            if add_state == "open":
                add_state, add_exit, add_exit_t = "closed", target, int(b.time[j])
            break
        if session_close and int(b.time[j]) + TF >= s.close_ts:
            exit_px = c
            if add_state == "open":
                add_state, add_exit, add_exit_t = "closed", c, int(b.time[j])
            break
        if breakeven and ((d > 0 and h >= add_level) or (d < 0 and lo <= add_level)):
            be_armed = True
        j += 1
    if exit_px is None:
        j = end
        exit_px = float(b.close[j])
        if add_state == "open":
            add_state, add_exit, add_exit_t = "closed", exit_px, int(b.time[j])
    spread_e = float(b.spread[e]) / sd
    r = d * (exit_px - entry) / sd - spread_e
    t_exit = int(b.time[j])
    if add_fill_bar is None:
        return (r, t_exit, None, None, None)
    add_r = d * (add_exit - add_level) / sd - float(b.spread[add_fill_bar]) / sd
    return (r, t_exit, add_r, int(b.time[add_fill_bar]), add_exit_t)


# ── walk-forward selection ───────────────────────────────────────────────────

def _score(rs: list[float]) -> float:
    return statistics.mean(rs) * math.sqrt(len(rs)) if rs else -math.inf


def in_window(setups: Iterable[Setup], lo: int, hi: int) -> list[Setup]:
    return [s for s in setups if lo <= s.t_entry < hi]


def rs(setups: Iterable[Setup], key: str) -> list[float]:
    return [s.outcomes[key][0] for s in setups]


def choose_config(setups_by_cfg: dict[tuple, list[Setup]], lo: int, hi: int,
                  min_n: int = 60) -> tuple[tuple, str] | None:
    best, best_score = None, 0.0
    for cfg, setups in setups_by_cfg.items():
        ins = in_window(setups, lo, hi)
        if len(ins) < min_n:
            continue
        for rr, sc, be in EXIT_VARIANTS:
            key = variant_key(rr, sc, be)
            sc_ = _score(rs(ins, key))
            if sc_ > best_score:
                best, best_score = (cfg, key), sc_
    return best


RANGES = (15, 30, 60)
RRS = (1.0, 1.5, 2.0, 3.0, 4.0)


def choose_config_robust(setups_by_cfg: dict, lo: int, hi: int, min_n: int = 60) -> tuple[tuple, str] | None:
    """Like choose_config, but a setting is only as good as its NEIGHBOURHOOD.

    With 3 ranges x 2 entries x 2 sessions x 12 exits there are 144 candidates per
    market, and the best in-sample one is partly luck — measured on GBPJPY, the
    widest search picked a 15-minute retest at 1:4 that lost on the holdout. Here
    a candidate scores min(its own score, median score of the adjacent range
    lengths and targets), so an isolated spike cannot win; a plateau can.
    Rule fixed before looking at any out-of-sample result.
    """
    scores: dict[tuple, float] = {}
    for cfg, setups in setups_by_cfg.items():
        if not isinstance(cfg, tuple):
            continue
        ins = in_window(setups, lo, hi)
        if len(ins) < min_n:
            continue
        for rr, sc, be in EXIT_VARIANTS:
            scores[(*cfg, rr, sc, be)] = _score(rs(ins, variant_key(rr, sc, be)))
    best, best_val = None, 0.0
    for (session, rng, mode, rr, sc, be), own in scores.items():
        ri, qi = RANGES.index(rng), RRS.index(rr)
        neigh = [scores[k] for a in (ri - 1, ri, ri + 1) if 0 <= a < len(RANGES)
                 for q in (qi - 1, qi, qi + 1) if 0 <= q < len(RRS)
                 if (k := (session, RANGES[a], mode, RRS[q], sc, be)) in scores]
        if len(neigh) < 3:
            continue
        val = min(own, statistics.median(neigh))
        if val > best_val:
            best, best_val = ((session, rng, mode), variant_key(rr, sc, be)), val
    return best


def choose_filter(setups: list[Setup], key: str, lo: int, hi: int,
                  min_frac: float = 0.35, min_n: int = 40) -> tuple[str, bool] | None:
    """One confluence, chosen in-sample: it must raise mean R enough to beat the
    unfiltered score despite fewer trades, keep enough trades, and be positive
    at least one standard error in BOTH halves of the in-sample window."""
    ins = in_window(setups, lo, hi)
    base = _score(rs(ins, key))
    best, best_score = None, base
    for f in FEATURES:
        for want in (True, False):
            sub = [s for s in ins if s.features[f] is want]
            if len(sub) < max(min_n, min_frac * len(ins)):
                continue
            r = rs(sub, key)
            h = len(r) // 2
            halves_ok = all(len(x) > 2 and statistics.mean(x) > 0 and
                            statistics.mean(x) / (statistics.stdev(x) / math.sqrt(len(x))) >= 1.0
                            for x in (r[:h], r[h:]))
            if halves_ok and _score(r) > best_score:
                best, best_score = (f, want), _score(r)
    return best
