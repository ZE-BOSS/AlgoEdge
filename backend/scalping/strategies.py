"""Scalpers S1-S7 (Phase 5 of the AlgoEdge upgrade plan).

Each strategy reads bars built from ticks and returns engine Orders stamped
with the time its decision was possible (the signal bar's last tick). Every
number is a parameter so the robustness grid can vary it.

Times in the source EAs are "server time"; here they are New York time for
Nasdaq (the session that matters) and UTC for gold.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend.scalping import indicators as I
from backend.scalping.engine import Order
from backend.scalping.ticks import Bars, Ticks, make_bars


@dataclass
class Instrument:
    name: str
    point: float = 0.01
    commission: float = 0.0        # round trip, price units per 1 unit traded


class BarCache:
    def __init__(self, tk: Ticks):
        self.tk = tk
        self._c: dict[int, Bars] = {}

    def __call__(self, seconds: int) -> Bars:
        if seconds not in self._c:
            self._c[seconds] = make_bars(self.tk, seconds)
        return self._c[seconds]


def _exit_at_bar(b: Bars, i_entry: int, j_exit: int, t_dec: int) -> float:
    """max_hold that ends the trade at the close of bar j (a bar-close exit rule)."""
    return max(1.0, (int(b.end_ms[j_exit]) - t_dec) / 1000.0)


# ── S1 gold basket scalper (from "XAUUSD MachineGun v2.1") ───────────────────
S1_DEFAULT = dict(tf=60, htf=3600, htf_ema=50, atr_n=14, box=12, box_min_atr=1.0, box_max_atr=4.0,
                  buf_atr=0.10, body_atr=0.35, clv=0.65, max_bar_atr=2.5, retest_bars=3,
                  pullback_ema=20, stop_atr=1.5, layers=(0.30, 0.55, 0.75), tp_r=2.0, be_r=1.0,
                  trail_r=1.0, max_spread=0.60, spread_share=0.15, start_utc=6, end_utc=18, flat_utc=19,
                  layer_expire_bars=15, entries=("A", "B", "C"))


def s1_gold_basket(bc: BarCache, p: dict, inst: Instrument) -> list[Order]:
    p = {**S1_DEFAULT, **p}
    b, hb = bc(p["tf"]), bc(p["htf"])
    if len(b) < 100 or len(hb) < p["htf_ema"] + 2:
        return []
    o, h, l, c = b.o, b.h, b.l, b.c  # noqa: E741
    a = I.atr(h, l, c, p["atr_n"])
    trend_val = I.higher_tf(b.t, hb.t, hb.c - I.ema(hb.c, p["htf_ema"]), p["htf"])
    hi, lo = I.roll_max(h, p["box"]), I.roll_min(l, p["box"])
    ema_pb = I.ema(c, p["pullback_ema"])
    cl = I.clv(o, h, l, c)
    hour = (b.t // 3600) % 24
    orders, gid = [], 0
    last_break = {1: (-10**9, 0.0), -1: (-10**9, 0.0)}      # side -> (bar, level)
    for i in range(max(p["box"], 30), len(b)):
        if not (p["start_utc"] <= hour[i] < p["end_utc"]) or np.isnan(trend_val[i]) or np.isnan(hi[i]):
            continue
        side = 1 if trend_val[i] > 0 else -1
        A = a[i]
        if A <= 0:
            continue
        D = p["stop_atr"] * A
        if b.spread[i] > p["max_spread"] or b.spread[i] > p["spread_share"] * D:
            continue
        body = abs(c[i] - o[i])
        box_h = hi[i] - lo[i]
        why = None
        # (A) breakout of the box
        if "A" in p["entries"] and p["box_min_atr"] * A <= box_h <= p["box_max_atr"] * A and (h[i] - l[i]) <= p["max_bar_atr"] * A \
                and body >= p["body_atr"] * A:
            if side > 0 and c[i] > hi[i] + p["buf_atr"] * A and cl[i] >= p["clv"]:
                why, last_break[1] = "breakout", (i, hi[i])
            elif side < 0 and c[i] < lo[i] - p["buf_atr"] * A and cl[i] <= 1 - p["clv"]:
                why, last_break[-1] = "breakout", (i, lo[i])
        # (B) retest of the broken level
        if why is None and "B" in p["entries"]:
            bi, lvl = last_break[side]
            if 0 < i - bi <= p["retest_bars"]:
                touched = (l[i] <= lvl <= h[i])
                held = (c[i] > lvl) if side > 0 else (c[i] < lvl)
                if touched and held:
                    why = "retest"
        # (C) pullback to the EMA, closing back with the trend
        if why is None and "C" in p["entries"]:
            if side > 0 and l[i] <= ema_pb[i] < c[i] and c[i] > o[i]:
                why = "pullback"
            elif side < 0 and h[i] >= ema_pb[i] > c[i] and c[i] < o[i]:
                why = "pullback"
        if why is None:
            continue
        gid += 1
        t = int(b.end_ms[i])
        ref = c[i] + (b.spread[i] if side > 0 else 0.0)      # ~ the price we pay
        sl = ref - side * D
        flat = ((int(b.t[i]) // 86400) * 86400 + p["flat_utc"] * 3600) * 1000
        hold = max(60.0, (flat - t) / 1000.0)
        dists = [1.0] + [1.0 - x for x in p["layers"]]
        wsum = sum(dists)
        common = dict(sl=sl, tp=ref + side * p["tp_r"] * D, be_at=p["be_r"] * D, be_offset=0.0,
                      trail_start=p["be_r"] * D, trail_dist=p["trail_r"] * D, max_hold_s=hold, group=gid,
                      tag=f"S1 {why}")
        orders.append(Order(t, side, kind="MARKET", weight=dists[0] / wsum, **common))
        for x, d in zip(p["layers"], dists[1:]):
            orders.append(Order(t, side, kind="LIMIT", price=ref - side * x * D,
                                expire_s=p["layer_expire_bars"] * p["tf"], weight=d / wsum, **common))
    return orders


# ── S2 Nasdaq momentum burst (from "APEX Velocity Scalp v2.02") ──────────────
S2_DEFAULT = dict(tf=60, ema_fast=8, ema_slow=21, htf=300, htf_ema=50, adx_n=14, adx_min=22.0, atr_n=14,
                  atr_min=3.0, atr_max=40.0, look=5, buf=0.8, body_atr=0.45, clv=0.6,
                  sl_atr=1.2, tp_atr=1.6, be_atr=0.8, be_plus=1.0, trail_atr=1.2, time_stop_min=12,
                  time_stop_r=0.3, abort=1.5, min_tp_cost=2.5,
                  windows=((8 * 60 + 30, 11 * 60), (13 * 60, 14 * 60 + 30)))


def s2_nasdaq_burst(bc: BarCache, p: dict, inst: Instrument) -> list[Order]:
    p = {**S2_DEFAULT, **p}
    b, hb = bc(p["tf"]), bc(p["htf"])
    if len(b) < 100:
        return []
    o, h, l, c = b.o, b.h, b.l, b.c  # noqa: E741
    a = I.atr(h, l, c, p["atr_n"])
    ef, es = I.ema(c, p["ema_fast"]), I.ema(c, p["ema_slow"])
    dx = I.adx(h, l, c, p["adx_n"])
    h50 = I.higher_tf(b.t, hb.t, I.ema(hb.c, p["htf_ema"]), p["htf"])
    hh, ll = I.roll_max(h, p["look"]), I.roll_min(l, p["look"])
    cl = I.clv(o, h, l, c)
    mins = I.ny_minutes(b.t)
    wd = ((b.t // 86400) + 4) % 7                  # 0 = Monday (1970-01-01 was a Thursday)
    orders = []
    for i in range(60, len(b)):
        if wd[i] >= 5 or not any(w0 <= mins[i] < w1 for w0, w1 in p["windows"]) or np.isnan(h50[i]) or np.isnan(hh[i]):
            continue
        A = a[i]
        if not (p["atr_min"] <= A <= p["atr_max"]) or dx[i] < p["adx_min"]:
            continue
        body = abs(c[i] - o[i])
        if body < p["body_atr"] * A:
            continue
        cost = b.spread[i] + inst.commission
        if p["tp_atr"] * A < p["min_tp_cost"] * cost:
            continue
        if ef[i] > es[i] and c[i] > h50[i] and c[i] > hh[i] + p["buf"] and cl[i] >= p["clv"]:
            side = 1
        elif ef[i] < es[i] and c[i] < h50[i] and c[i] < ll[i] - p["buf"] and cl[i] <= 1 - p["clv"]:
            side = -1
        else:
            continue
        t = int(b.end_ms[i])
        ref = c[i] + (b.spread[i] if side > 0 else 0.0)
        orders.append(Order(t, side, sl=ref - side * p["sl_atr"] * A, tp=ref + side * p["tp_atr"] * A,
                            price=ref, max_slip=p["abort"], be_at=p["be_atr"] * A, be_offset=p["be_plus"],
                            trail_start=p["be_atr"] * A, trail_dist=p["trail_atr"] * A,
                            time_stop_s=p["time_stop_min"] * 60, time_stop_min_r=p["time_stop_r"],
                            max_hold_s=3600, tag="S2"))
    return orders


# ── S3 ATR trailing-stop flip (UT Bot) ──────────────────────────────────────
S3_DEFAULT = dict(tf=60, key=2.0, atr_n=10, htf=3600, htf_ema=50, use_trend=True, exit="flip",
                  sl_atr=1.5, tp_atr=3.0, hours_utc=None, max_hold_min=240)


def ut_bot(c: np.ndarray, a: np.ndarray, key: float) -> np.ndarray:
    """UT Bot trailing line; position +1/-1 per bar."""
    stop = np.empty(len(c))
    pos = np.zeros(len(c), dtype=np.int8)
    stop[0] = c[0]
    for i in range(1, len(c)):
        nl = key * a[i]
        ps = stop[i - 1]
        if c[i] > ps and c[i - 1] > ps:
            stop[i] = max(ps, c[i] - nl)
        elif c[i] < ps and c[i - 1] < ps:
            stop[i] = min(ps, c[i] + nl)
        elif c[i] > ps:
            stop[i] = c[i] - nl
        else:
            stop[i] = c[i] + nl
        if c[i - 1] < ps and c[i] > ps:
            pos[i] = 1
        elif c[i - 1] > ps and c[i] < ps:
            pos[i] = -1
        else:
            pos[i] = pos[i - 1]
    return pos


def s3_ut_flip(bc: BarCache, p: dict, inst: Instrument) -> list[Order]:
    p = {**S3_DEFAULT, **p}
    b, hb = bc(p["tf"]), bc(p["htf"])
    if len(b) < 100:
        return []
    h, l, c = b.h, b.l, b.c  # noqa: E741
    a = I.atr(h, l, c, p["atr_n"])
    pos = ut_bot(c, a, p["key"])
    trend = I.higher_tf(b.t, hb.t, hb.c - I.ema(hb.c, p["htf_ema"]), p["htf"]) if p["use_trend"] else None
    hour = (b.t // 3600) % 24
    flips = np.nonzero(np.r_[False, pos[1:] != pos[:-1]] & (pos != 0))[0]
    orders = []
    for n, i in enumerate(flips):
        side = int(pos[i])
        if trend is not None and (np.isnan(trend[i]) or np.sign(trend[i]) != side):
            continue
        if p["hours_utc"] and not (p["hours_utc"][0] <= hour[i] < p["hours_utc"][1]):
            continue
        t = int(b.end_ms[i])
        ref = c[i] + (b.spread[i] if side > 0 else 0.0)
        A = a[i]
        if p["exit"] == "flip":
            j = flips[n + 1] if n + 1 < len(flips) else len(b) - 1
            hold = min(_exit_at_bar(b, i, j, t), p["max_hold_min"] * 60)
            orders.append(Order(t, side, sl=ref - side * p["sl_atr"] * A, max_hold_s=hold, tag="S3 flip"))
        else:
            orders.append(Order(t, side, sl=ref - side * p["sl_atr"] * A, tp=ref + side * p["tp_atr"] * A,
                                max_hold_s=p["max_hold_min"] * 60, tag="S3 target"))
    return orders


# ── S4 full-retracement fade ────────────────────────────────────────────────
S4_DEFAULT = dict(tf=60, atr_n=14, impulse_bars=10, impulse_atr=5.0, wait_bars=10, wick_body=2.0, wick_atr=0.5,
                  sl_buf_atr=0.1, max_hold_min=180, hours_utc=(6, 20))


def s4_retrace_fade(bc: BarCache, p: dict, inst: Instrument) -> list[Order]:
    p = {**S4_DEFAULT, **p}
    b = bc(p["tf"])
    if len(b) < 100:
        return []
    o, h, l, c = b.o, b.h, b.l, b.c  # noqa: E741
    a = I.atr(h, l, c, p["atr_n"])
    hour = (b.t // 3600) % 24
    n = p["impulse_bars"]
    orders, gid, i = [], 0, n + 30
    while i < len(b):
        A = a[i - 1]
        seg_h, seg_l = h[i - n:i].max(), l[i - n:i].min()
        up = c[i - 1] - o[i - n]
        if abs(up) < p["impulse_atr"] * A or not (p["hours_utc"][0] <= hour[i] < p["hours_utc"][1]):
            i += 1
            continue
        imp = 1 if up > 0 else -1                      # the move to fade
        origin = o[i - n]
        extreme = seg_h if imp > 0 else seg_l
        taken = False
        for j in range(i, min(i + p["wait_bars"], len(b))):
            body = abs(c[j] - o[j])
            upper, lower = h[j] - max(o[j], c[j]), min(o[j], c[j]) - l[j]
            wick = upper if imp > 0 else lower
            rej = wick >= p["wick_body"] * max(body, 1e-9) and wick >= p["wick_atr"] * A
            engulf = (imp > 0 and c[j] < o[j] and c[j] < o[j - 1] and o[j] >= c[j - 1]) or \
                     (imp < 0 and c[j] > o[j] and c[j] > o[j - 1] and o[j] <= c[j - 1])
            if rej or engulf:
                side = -imp
                ext = max(extreme, h[j]) if imp > 0 else min(extreme, l[j])
                t = int(b.end_ms[j])
                sl = ext + imp * (p["sl_buf_atr"] * A + b.spread[j])
                half = (c[j] + origin) / 2
                gid += 1
                common = dict(sl=sl, max_hold_s=p["max_hold_min"] * 60, group=gid, tag="S4")
                orders.append(Order(t, side, tp=half, weight=0.5, **common))
                orders.append(Order(t, side, tp=origin, weight=0.5, be_at=abs(c[j] - half), **common))
                i, taken = j + 1, True
                break
        if not taken:
            i += 1
    return orders


# ── S5 swing-level stop-run ─────────────────────────────────────────────────
S5_DEFAULT = dict(tf=60, swing_tf=300, k=2, vol_mult=2.0, vol_n=20, mode="fade", atr_n=14, sl_buf_atr=0.1,
                  tp_r=2.0, cont_sl_atr=1.0, max_age_h=12, max_hold_min=120, hours_utc=None)


def s5_stop_run(bc: BarCache, p: dict, inst: Instrument) -> list[Order]:
    p = {**S5_DEFAULT, **p}
    b, sb = bc(p["tf"]), bc(p["swing_tf"])
    if len(b) < 100 or len(sb) < 20:
        return []
    o, h, l, c = b.o, b.h, b.l, b.c  # noqa: E741
    a = I.atr(h, l, c, p["atr_n"])
    vavg = I.roll_mean(b.n.astype(float), p["vol_n"])
    fh, fl = I.fractal_highs(sb.h, p["k"]), I.fractal_lows(sb.l, p["k"])
    # a swing is known once k bars after it have CLOSED
    known_at = sb.t + (p["k"] + 1) * p["swing_tf"]
    highs = [(known_at[i], sb.t[i], sb.h[i]) for i in np.nonzero(fh)[0]]
    lows = [(known_at[i], sb.t[i], sb.l[i]) for i in np.nonzero(fl)[0]]
    hour = (b.t // 3600) % 24
    orders = []
    used: set = set()
    hi_ptr = lo_ptr = 0
    live_hi: list = []
    live_lo: list = []
    for i in range(30, len(b) - 1):
        now = b.t[i]
        while hi_ptr < len(highs) and highs[hi_ptr][0] <= now:
            live_hi.append(highs[hi_ptr])
            hi_ptr += 1
        while lo_ptr < len(lows) and lows[lo_ptr][0] <= now:
            live_lo.append(lows[lo_ptr])
            lo_ptr += 1
        live_hi = [x for x in live_hi if now - x[1] <= p["max_age_h"] * 3600 and x not in used]
        live_lo = [x for x in live_lo if now - x[1] <= p["max_age_h"] * 3600 and x not in used]
        if p["hours_utc"] and not (p["hours_utc"][0] <= hour[i] < p["hours_utc"][1]):
            continue
        if np.isnan(vavg[i]) or b.n[i] < p["vol_mult"] * vavg[i]:
            continue
        A = a[i]
        for lvl_list, brk in ((live_hi, 1), (live_lo, -1)):
            hit = [x for x in lvl_list if (h[i] > x[2] if brk > 0 else l[i] < x[2])]
            if not hit:
                continue
            lvl = max(hit, key=lambda x: x[2] * brk)
            used.add(lvl)
            level = lvl[2]
            if p["mode"] == "fade":
                closed_back = c[i] < level if brk > 0 else c[i] > level
                if not closed_back:
                    continue
                side = -brk
                tip = h[i] if brk > 0 else l[i]
                sl = tip + brk * (p["sl_buf_atr"] * A + b.spread[i])
                t = int(b.end_ms[i])
                ref = c[i] + (b.spread[i] if side > 0 else 0.0)
                tp = ref + side * p["tp_r"] * abs(ref - sl)
                orders.append(Order(t, side, sl=sl, tp=tp, max_hold_s=p["max_hold_min"] * 60, tag="S5 fade"))
            else:
                beyond = (c[i] > level and c[i + 1] > level) if brk > 0 else (c[i] < level and c[i + 1] < level)
                if not beyond:
                    continue
                side = brk
                t = int(b.end_ms[i + 1])
                ref = c[i + 1] + (b.spread[i + 1] if side > 0 else 0.0)
                sl = ref - side * p["cont_sl_atr"] * A
                orders.append(Order(t, side, sl=sl, tp=ref + side * p["tp_r"] * abs(ref - sl),
                                    max_hold_s=p["max_hold_min"] * 60, tag="S5 continuation"))
    return orders


# ── S6 EMA9 / VWAP cross (LuxAlgo) ──────────────────────────────────────────
S6_DEFAULT = dict(tf=180, ema_n=9, session="ny", exit="ema", atr_n=14, sl_atr=1.5, tp_r=3.0,
                  chop_n=14, chop_max=61.8, use_chop=False, max_hold_min=240, rth_only=True)


def chop(h, l, c, n):  # noqa: E741
    tr = I.true_range(h, l, c)
    s = np.convolve(tr, np.ones(n), "full")[:len(tr)]
    hh, ll = I.roll_max(h, n), I.roll_min(l, n)
    rng = np.where(hh - ll <= 0, np.nan, hh - ll)
    return 100 * np.log10(s / rng) / np.log10(n)


def s6_ema_vwap(bc: BarCache, p: dict, inst: Instrument) -> list[Order]:
    p = {**S6_DEFAULT, **p}
    b = bc(p["tf"])
    if len(b) < 60:
        return []
    h, l, c = b.h, b.l, b.c  # noqa: E741
    key = I.ny_day if p["session"] == "ny" else I.utc_day
    vw = I.session_vwap(b.t, h, l, c, b.n.astype(float), key)
    e = I.ema(c, p["ema_n"])
    a = I.atr(h, l, c, p["atr_n"])
    ch = chop(h, l, c, p["chop_n"]) if p["use_chop"] else None
    mins = I.ny_minutes(b.t)
    diff = e - vw
    cross = np.nonzero(np.r_[False, np.sign(diff[1:]) != np.sign(diff[:-1])])[0]
    orders = []
    for i in cross:
        side = 1 if diff[i] > 0 else -1
        if p["rth_only"] and not (9 * 60 + 30 + p["tf"] // 60 <= mins[i] < 15 * 60 + 45):
            continue
        if ch is not None and (np.isnan(ch[i]) or ch[i] > p["chop_max"]):
            continue
        t = int(b.end_ms[i])
        ref = c[i] + (b.spread[i] if side > 0 else 0.0)
        A = a[i]
        if p["exit"] == "ema":
            # the video: out when a candle closes back through EMA9
            j = i + 1
            while j < len(b) and ((c[j] >= e[j]) if side > 0 else (c[j] <= e[j])) and \
                    (not p["rth_only"] or mins[j] < 15 * 60 + 55):
                j += 1
            j = min(j, len(b) - 1)
            hold = min(_exit_at_bar(b, i, j, t), p["max_hold_min"] * 60)
            orders.append(Order(t, side, sl=ref - side * 3 * A, max_hold_s=hold, tag="S6 ema exit"))
        else:
            sl = ref - side * p["sl_atr"] * A
            orders.append(Order(t, side, sl=sl, tp=ref + side * p["tp_r"] * p["sl_atr"] * A,
                                max_hold_s=p["max_hold_min"] * 60, tag="S6 atr exit"))
    return orders


# ── S7 Crash/Boom spike counting ────────────────────────────────────────────
def spikes(tk: Ticks, kind: str, mult: float = 8.0) -> np.ndarray:
    """Tick indices of spikes: a single-tick move in the spike direction larger
    than `mult` x the median absolute tick move."""
    d = np.diff(tk.bid)
    med = np.median(np.abs(d[d != 0])) if np.any(d != 0) else 0.0
    if med == 0:
        return np.zeros(0, dtype=np.int64)
    sgn = 1 if kind == "boom" else -1
    return np.nonzero(d * sgn > mult * med)[0] + 1


def hazard(tk: Ticks, kind: str, bins=(0, 50, 100, 200, 300, 400, 500, 700, 1000, 1500, 2000, 4000)) -> dict:
    """Chance of a spike within the next 20 ticks, by ticks since the last
    spike. Flat = memoryless (counting adds nothing)."""
    sp = spikes(tk, kind)
    if len(sp) < 10:
        return {"spikes": int(len(sp))}
    gaps = np.diff(sp)
    out = {"spikes": int(len(sp)), "mean_gap_ticks": round(float(gaps.mean()), 1),
           "median_gap_ticks": int(np.median(gaps)), "by_ticks_since": []}
    base = 20.0 / gaps.mean()
    for a, b in zip(bins[:-1], bins[1:]):
        at_risk = np.clip(gaps - a, 0, b - a).sum()           # tick-steps spent in [a, b)
        events = ((gaps >= a + 1) & (gaps <= b)).sum()
        if at_risk < 2000:
            continue
        out["by_ticks_since"].append({"from": a, "to": b, "p_spike_next_20": round(20 * events / at_risk, 4),
                                      "vs_base": round(20 * events / at_risk / base, 2)})
    return out


S7_DEFAULT = dict(kind="boom", wait_ticks=300, hold_ticks=200, sl_mult=None, tp_spike=True)


def s7_spike_count(tk: Ticks, p: dict, inst: Instrument) -> list[Order]:
    """Boom: buy once `wait_ticks` have passed since the last spike, hold up to
    `hold_ticks` ticks, out on the spike (target just above the entry by a
    spike's typical size) or the time limit. Crash: the mirror."""
    p = {**S7_DEFAULT, **p}
    sp = spikes(tk, p["kind"])
    if len(sp) < 20:
        return []
    side = 1 if p["kind"] == "boom" else -1
    d = np.diff(tk.bid)
    typical = float(np.median(np.abs(d[sp - 1])))
    step = float(np.median(np.abs(d[d != 0])))
    orders = []
    for k in range(len(sp) - 1):
        i = sp[k] + p["wait_ticks"]
        if i >= sp[k + 1] or i >= len(tk):                 # the next spike came first: nothing to count
            continue
        t = int(tk.t[i])
        ref = float(tk.ask[i] if side > 0 else tk.bid[i])
        hold_ms = int(tk.t[min(i + p["hold_ticks"], len(tk) - 1)] - t)
        sl_dist = (p["sl_mult"] or 400) * step
        tp = ref + side * 0.5 * typical if p["tp_spike"] else None
        orders.append(Order(t, side, sl=ref - side * sl_dist, tp=tp, max_hold_s=max(1.0, hold_ms / 1000),
                            tag=f"S7 {p['kind']}"))
    return orders


STRATEGIES = {"S1": s1_gold_basket, "S2": s2_nasdaq_burst, "S3": s3_ut_flip, "S4": s4_retrace_fade,
              "S5": s5_stop_run, "S6": s6_ema_vwap}
