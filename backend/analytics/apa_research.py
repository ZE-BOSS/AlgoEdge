"""
backend/analytics/apa_research.py

APA_v1 (Head & Shoulders / ABC reversal with an invalidation-zone entry), taken
apart the same way vwap_research.py takes VWAP apart.

THE POINT
---------
APA's own docstrings record two expensive lessons: requiring a retest filtered
OUT the breakouts that worked (192 expired waiting, the 15 that retested were the
failures), and requiring a rejection candle cut the population to 3 signals in
20,000 bars. Both were discovered by flipping one flag and re-running everything.
This module makes that kind of question cheap: build the candidate list ONCE with
every optional gate recorded rather than enforced, then answer any combination by
filtering — including the ones that are path-dependent.

Structure detection runs on the STRUCTURE timeframe (M15 by default) and entries
and exits resolve on the ENTRY timeframe (M5), exactly as the live engine splits
them, so a fill here is a fill there.

WHAT IS RECORDED PER CANDIDATE
------------------------------
    symmetry_gap_atr        |left shoulder - right shoulder| in ATR
    neckline_precision_atr  distance from the neckline to the nearest major swing
    bos_body_atr            how decisive the breaking candle was
    retest_occurred         price returned into the invalidation zone in time
    zone_rejected           a wick into the zone that closed back out
    head_not_breached       the head level still held at entry
    session_ok              inside the configured session window
    tight_levels            the stop came from the head wick, not the shoulder
    trend_align             the break agrees with the 20-day trend
    entry_near_neckline     entry within 1 ATR of the broken neckline

EXITS
-----
Fixed 1:1 .. 1:10 (the 1:5 / 1:7 / 1:10 the user trades), plus a scale-out that
takes a third at 2R, moves the stop to entry and runs the remainder — the change
the 2026-09-11 report proposed for making high R:R tradable — and the head-close
invalidation exit on or off.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import numpy as np

from backend.analytics.strategy_search import Bars, atr
from backend.analytics.vwap_research import in_window, score, stats  # generic, read .outcomes/.t_entry

MAX_HOLD_ENTRY_BARS = 576          # 48h on M5
SCALE_FIRST_R = 2.0                # first third exits here
SCALE_FRACTION = 1.0 / 3.0

EXIT_VARIANTS: tuple[tuple[Any, bool], ...] = tuple(
    [(rr, True) for rr in (1.0, 2.0, 3.0, 5.0, 7.0, 10.0)]
    + [("scaleout5", True), ("scaleout10", True), (3.0, False), (5.0, False), ("scaleout10", False)]
)

CONFLUENCES = ("retest_occurred", "zone_rejected", "head_not_breached", "session_ok",
               "tight_levels", "trend_align", "entry_near_neckline", "symmetry_tight",
               "neckline_precise", "bos_decisive")


def variant_key(target: Any, head_exit: bool) -> str:
    t = f"1:{target:g}" if not isinstance(target, str) else target
    return f"{t}{'' if head_exit else ' no-head-exit'}"


@dataclass(frozen=True)
class APAConfig:
    minor_fractal_m: int = 3
    major_fractal_m: int = 8
    symmetry_tolerance_atr: float = 0.3
    neckline_major_atr_tolerance: float = 1.0
    invalidation_zone_source: str = "right_shoulder"      # right_shoulder | both | left_shoulder
    tight_level_threshold_atr: float = 0.35
    sl_buffer_atr: float = 0.05
    sl_buffer_atr_mult: float = 0.5
    min_sl_atr_mult: float = 0.0          # research knob; the live engine leaves these inert
    max_sl_floor_atr_mult: float = 5.0
    min_sl_spread_mult: float = 4.0
    """Stop floor as a multiple of the entry bar's spread. APA's own params.py
    records why this matters: its structural stop sits at the shoulder wick while
    the entry sits inside the invalidation zone, and the two "can be a fraction of
    a pip apart" — a 0.45-pip stop on a 2-pip spread. Without a floor those become
    division artefacts worth hundreds of R."""

    min_stop_atr_discard: float = 0.02
    """Below this fraction of ATR the stop is an artefact; drop the candidate
    rather than invent a wider one."""
    atr_lookback: int = 14
    pattern_max_age_bars: int = 40         # structure bars in AWAIT_BOS
    bos_max_age_bars: int = 30             # entry bars waiting for a retest
    retest_max_age_bars: int = 10          # entry bars waiting for confirmation
    session_start: str = "07:00"           # UTC, as the live engine uses
    session_cutoff: str = "16:00"
    max_concurrent_patterns: int = 3


@dataclass
class Candidate:
    symbol: str
    direction: int                 # +1 bullish (BUY), -1 bearish (SELL)
    t_bos: int
    i_entry: int                   # index into the ENTRY bars
    t_entry: int
    entry: float
    stop_dist: float
    neckline: float
    head: float
    iz_top: float
    iz_bottom: float
    features: dict[str, bool]
    detail: dict[str, float]
    outcomes: dict[str, tuple] = field(default_factory=dict)


# ── structure detection, mirroring core/swing_structure.py ───────────────────

def detect_swings(b: Bars, m: int) -> list[dict[str, Any]]:
    n = len(b)
    if n < 2 * m + 1:
        return []
    hi, lo = b.high, b.low
    is_high = np.ones(n, dtype=bool)
    is_low = np.ones(n, dtype=bool)
    for shift in range(1, m + 1):
        left = np.full(n, np.inf)
        left[shift:] = hi[:-shift]
        is_high &= hi > left
        right = np.full(n, np.inf)
        right[:-shift] = hi[shift:]
        is_high &= hi > right
        left_l = np.full(n, -np.inf)
        left_l[shift:] = lo[:-shift]
        is_low &= lo < left_l
        right_l = np.full(n, -np.inf)
        right_l[:-shift] = lo[shift:]
        is_low &= lo < right_l
    is_high[:m] = is_high[-m:] = False
    is_low[:m] = is_low[-m:] = False
    out = []
    for i in np.flatnonzero(is_high):
        out.append({"type": "HIGH", "price": float(hi[i]), "i": int(i),
                    "body_high": float(max(b.open[i], b.close[i])),
                    "body_low": float(min(b.open[i], b.close[i]))})
    for i in np.flatnonzero(is_low):
        out.append({"type": "LOW", "price": float(lo[i]), "i": int(i),
                    "body_high": float(max(b.open[i], b.close[i])),
                    "body_low": float(min(b.open[i], b.close[i]))})
    out.sort(key=lambda s: s["i"])
    return out


def detect_hs(swings: list[dict[str, Any]], atr_val: float, tol_atr: float) -> dict[str, Any] | None:
    """The engine's rule: only the most recent triplet, head beyond both
    shoulders, shoulders within tolerance, neckline the extreme between them."""
    tol = tol_atr * atr_val
    highs = [s for s in swings if s["type"] == "HIGH"]
    lows = [s for s in swings if s["type"] == "LOW"]
    if len(highs) >= 3:
        ls, head, rs = highs[-3], highs[-2], highs[-1]
        if head["price"] > ls["price"] and head["price"] > rs["price"] and abs(ls["price"] - rs["price"]) <= tol:
            between = [s for s in lows if ls["i"] < s["i"] < rs["i"]]
            if between:
                neck = min(between, key=lambda s: s["price"])
                return {"type": "BEARISH", "ls": ls, "head": head, "rs": rs, "neck": neck}
    if len(lows) >= 3:
        ls, head, rs = lows[-3], lows[-2], lows[-1]
        if head["price"] < ls["price"] and head["price"] < rs["price"] and abs(ls["price"] - rs["price"]) <= tol:
            between = [s for s in highs if ls["i"] < s["i"] < rs["i"]]
            if between:
                neck = max(between, key=lambda s: s["price"])
                return {"type": "BULLISH", "ls": ls, "head": head, "rs": rs, "neck": neck}
    return None


def _daily_sma(b: Bars, days: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """Prior-day-only SMA of daily closes, aligned to each bar (no look-ahead)."""
    day = b.time // 86400
    starts = np.flatnonzero(np.concatenate(([True], day[1:] != day[:-1])))
    ends = np.r_[starts[1:], len(day)]
    closes = b.close[ends - 1]
    sma = np.array([closes[max(0, k - days + 1):k + 1].mean() for k in range(len(closes))])
    per_bar = np.full(len(b), np.nan)
    for k, (s, e) in enumerate(zip(starts, ends)):
        per_bar[s:e] = sma[k - 1] if k >= 1 else np.nan
    return per_bar, day


def _hhmm_utc(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%H:%M")


# ── candidate generation ─────────────────────────────────────────────────────

def build_candidates(sb: Bars, eb: Bars, cfg: APAConfig = APAConfig()) -> list[Candidate]:
    """One pass over the structure bars for patterns and breaks, then the entry
    bars for the retest / confirmation window. Nothing optional is enforced."""
    s_atr = atr(sb, cfg.atr_lookback)
    minor_all = detect_swings(sb, cfg.minor_fractal_m)
    major_all = detect_swings(sb, cfg.major_fractal_m)
    sma20, _ = _daily_sma(sb, 20)
    e_time = eb.time
    out: list[Candidate] = []
    seen: set[tuple] = set()
    tf_e = int(e_time[1] - e_time[0]) if len(e_time) > 1 else 300

    for i in range(cfg.major_fractal_m * 2 + 5, len(sb)):
        a = float(s_atr[i]) if np.isfinite(s_atr[i]) else 0.0
        if a <= 0:
            continue
        # Swings are only visible once their right-hand window has closed.
        minor = [s for s in minor_all if s["i"] <= i - cfg.minor_fractal_m]
        pat = detect_hs(minor, a, cfg.symmetry_tolerance_atr)
        if not pat:
            continue
        d = -1 if pat["type"] == "BEARISH" else 1
        neck = float(pat["neck"]["price"])
        ident = (pat["type"], round(neck, 6), round(float(pat["head"]["price"]), 6))
        if ident in seen:
            continue

        # BOS: a structure-bar body close beyond the neckline, within the age budget.
        bos_i = None
        for j in range(i, min(len(sb), i + max(1, cfg.pattern_max_age_bars))):
            if (d < 0 and sb.close[j] < neck) or (d > 0 and sb.close[j] > neck):
                bos_i = j
                break
        if bos_i is None:
            continue
        seen.add(ident)
        a_bos = float(s_atr[bos_i]) if np.isfinite(s_atr[bos_i]) else a
        majors = [s for s in major_all if s["i"] <= bos_i - cfg.major_fractal_m]
        nearest = min((abs(s["price"] - neck) for s in majors), default=None)
        if nearest is None or a_bos <= 0 or nearest > cfg.neckline_major_atr_tolerance * a_bos:
            continue          # liquidity sweep, not a level — the engine drops these

        rs, ls, head = pat["rs"], pat["ls"], pat["head"]
        if cfg.invalidation_zone_source == "both":
            zs = [rs, ls]
        elif cfg.invalidation_zone_source == "left_shoulder":
            zs = [ls]
        else:
            zs = [rs]
        iz_top = max(s["body_high"] for s in zs)
        iz_bottom = min(s["body_low"] for s in zs)

        tight = abs(head["price"] - rs["price"]) < cfg.tight_level_threshold_atr * a_bos
        wick = float(head["price"] if tight else rs["price"])
        buf = (cfg.sl_buffer_atr + cfg.sl_buffer_atr_mult) * a_bos
        sl_level = wick + buf if d < 0 else wick - buf

        # Entry timeframe: first bar after the BOS bar closes.
        t_bos_close = int(sb.time[bos_i]) + int(sb.time[1] - sb.time[0])
        k0 = int(np.searchsorted(e_time, t_bos_close, side="left"))
        if k0 >= len(eb) - 1:
            continue

        # Retest / rejection observation window (recorded, never enforced).
        retest_occurred, zone_rejected, retest_k = False, False, None
        for k in range(k0, min(len(eb), k0 + max(1, cfg.bos_max_age_bars))):
            body_top = max(float(eb.open[k]), float(eb.close[k]))
            body_bot = min(float(eb.open[k]), float(eb.close[k]))
            if body_bot <= iz_top and body_top >= iz_bottom:
                retest_occurred, retest_k = True, k
                if d < 0:
                    zone_rejected = bool(eb.high[k] >= iz_bottom and eb.close[k] < iz_bottom)
                else:
                    zone_rejected = bool(eb.low[k] <= iz_top and eb.close[k] > iz_top)
                break

        entry_i = k0
        entry = float(eb.open[entry_i])
        stop_dist = abs(entry - sl_level)
        if cfg.min_sl_spread_mult > 0:
            stop_dist = max(stop_dist, cfg.min_sl_spread_mult * float(eb.spread[entry_i]))
        if cfg.min_sl_atr_mult > 0:
            floor = min(cfg.min_sl_atr_mult * a_bos, cfg.max_sl_floor_atr_mult * a_bos)
            stop_dist = max(stop_dist, floor)
        if not (stop_dist > 0 and math.isfinite(stop_dist)):
            continue
        if cfg.min_stop_atr_discard > 0 and stop_dist < cfg.min_stop_atr_discard * a_bos:
            continue

        bos_body = abs(float(sb.close[bos_i]) - float(sb.open[bos_i])) / a_bos if a_bos > 0 else 0.0
        hhmm = _hhmm_utc(int(eb.time[entry_i]))
        trend = sma20[bos_i]
        feats = {
            "retest_occurred": retest_occurred,
            "zone_rejected": zone_rejected,
            "head_not_breached": (float(eb.close[entry_i]) < head["price"]) if d < 0
            else (float(eb.close[entry_i]) > head["price"]),
            "session_ok": cfg.session_start <= hhmm < cfg.session_cutoff,
            "tight_levels": tight,
            "trend_align": bool(np.isfinite(trend) and ((d > 0) == (float(sb.close[bos_i]) > trend))),
            "entry_near_neckline": abs(entry - neck) <= a_bos,
            "symmetry_tight": abs(ls["price"] - rs["price"]) <= 0.15 * a_bos,
            "neckline_precise": nearest <= 0.30 * a_bos,
            "bos_decisive": bos_body >= 0.5,
        }
        c = Candidate(
            symbol=sb.symbol, direction=d, t_bos=int(sb.time[bos_i]),
            i_entry=entry_i, t_entry=int(eb.time[entry_i]), entry=entry,
            stop_dist=float(stop_dist), neckline=neck, head=float(head["price"]),
            iz_top=float(iz_top), iz_bottom=float(iz_bottom), features=feats,
            detail={"atr": a_bos, "symmetry_gap_atr": abs(ls["price"] - rs["price"]) / a_bos,
                    "neckline_precision_atr": nearest / a_bos, "bos_body_atr": bos_body,
                    "stop_atr": stop_dist / a_bos,
                    "retest_bars": float(retest_k - k0) if retest_k is not None else float("nan")},
        )
        _resolve_all(eb, c, tf_e)
        out.append(c)
    return out


# ── outcome resolution, including the scale-out ──────────────────────────────

class _Lists:
    def __init__(self, b: Bars):
        self.open, self.high, self.low = b.open.tolist(), b.high.tolist(), b.low.tolist()
        self.close, self.time, self.spread = b.close.tolist(), b.time.tolist(), b.spread.tolist()


_CACHE: dict[int, tuple[Bars, _Lists]] = {}


def _lists(b: Bars) -> _Lists:
    hit = _CACHE.get(id(b))
    if hit is None or hit[0] is not b:
        _CACHE.clear()
        hit = _CACHE[id(b)] = (b, _Lists(b))
    return hit[1]


def _resolve_all(eb: Bars, c: Candidate, tf: int) -> None:
    bl = _lists(eb)
    for target, head_exit in EXIT_VARIANTS:
        c.outcomes[variant_key(target, head_exit)] = _resolve(bl, c, target, head_exit)


def _resolve(bl: _Lists, c: Candidate, target: Any, head_exit: bool) -> tuple:
    """One bar walk. Ties lose, gaps fill at the open, and the head-close
    invalidation exit fires on the bar's close when the body crosses the head."""
    n, d, e, entry, sd = len(bl.close), c.direction, c.i_entry, c.entry, c.stop_dist
    scale = isinstance(target, str) and target.startswith("scaleout")
    runner_rr = float(str(target).replace("scaleout", "")) if scale else float(target)
    stop = entry - d * sd
    first_tp = entry + d * SCALE_FIRST_R * sd if scale else None
    final_tp = entry + d * runner_rr * sd
    booked, remaining = 0.0, 1.0
    end = min(n - 1, e + MAX_HOLD_ENTRY_BARS)
    j, exit_px, reason = e, None, ""
    while j <= end:
        o, h, lo, cl = bl.open[j], bl.high[j], bl.low[j], bl.close[j]
        if j > e:
            if (d > 0 and o <= stop) or (d < 0 and o >= stop):
                exit_px, reason = o, "STOP_GAP"
                break
            if (d > 0 and o >= final_tp) or (d < 0 and o <= final_tp):
                exit_px, reason = o, "TARGET_GAP"
                break
        if (d > 0 and lo <= stop) or (d < 0 and h >= stop):
            exit_px, reason = stop, "STOP"
            break
        if scale and remaining == 1.0 and ((d > 0 and h >= first_tp) or (d < 0 and lo <= first_tp)):
            booked += SCALE_FRACTION * SCALE_FIRST_R
            remaining = 1.0 - SCALE_FRACTION
            stop = entry                      # the rest runs risk-free
        if (d > 0 and h >= final_tp) or (d < 0 and lo <= final_tp):
            exit_px, reason = final_tp, "TARGET"
            break
        if head_exit and ((d < 0 and max(o, cl) > c.head) or (d > 0 and min(o, cl) < c.head)):
            exit_px, reason = cl, "HEAD_INVALIDATION"
            break
        j += 1
    if exit_px is None:
        j = end
        exit_px, reason = bl.close[j], "TIME"
    r = booked + remaining * (d * (exit_px - entry) / sd) - bl.spread[e] / sd
    return (r, int(bl.time[j]), reason)


# ── combinations and ablation ────────────────────────────────────────────────

def apply_combination(cands: Sequence[Candidate], *, use: Iterable[str] = (),
                      max_per_day: int = 0) -> list[Candidate]:
    want = tuple(use)
    picked, per_day = [], {}
    for c in sorted(cands, key=lambda c: c.t_entry):
        if any(not c.features[f] for f in want):
            continue
        if max_per_day:
            day = c.t_entry // 86400
            if per_day.get(day, 0) >= max_per_day:
                continue
            per_day[day] = per_day.get(day, 0) + 1
        picked.append(c)
    return picked


def ablate(cands: Sequence[Candidate], key: str, *, base: Iterable[str] = ()) -> list[dict[str, Any]]:
    base = tuple(base)
    rows = []
    for f in CONFLUENCES:
        on = apply_combination(cands, use=tuple(dict.fromkeys(base + (f,))))
        off = apply_combination(cands, use=tuple(x for x in base if x != f))
        blocked = [c for c in off if not c.features[f]]
        s_on, s_off, s_blk = stats(on, key), stats(off, key), stats(blocked, key)
        rows.append({
            "confluence": f, "n_on": s_on["n"], "avg_r_on": s_on["avg_r"],
            "n_blocked": s_blk["n"], "avg_r_blocked": s_blk["avg_r"],
            "avg_r_without": s_off["avg_r"],
            "edge_added": None if (s_on["avg_r"] is None or s_off["avg_r"] is None)
            else round(s_on["avg_r"] - s_off["avg_r"], 4),
        })
    rows.sort(key=lambda r: -(r["edge_added"] or -9))
    return rows
