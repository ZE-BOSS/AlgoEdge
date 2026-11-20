"""Tick-level execution of strategy orders.

A strategy decides on bars (built from ticks) and emits Orders stamped with
the moment its decision was possible (the closing tick of the signal bar).
Here every order is executed on the real tick stream:

  entry   MARKET: first tick >= decision + latency; a buy pays the ASK, a sell
          the BID. LIMIT/STOP: first tick that trades through the price, within
          `expire_s`. A market order whose fill is more than `max_slip` worse
          than the signal price is aborted (S2's "price ran away").
  exits   on the tick that touches them, no same-bar guessing:
          stop   long: BID <= stop -> filled at that BID (gaps slip);  short: ASK >= stop
          target long: BID >= tp   -> filled at tp;                    short: ASK <= tp
          breakeven  once the move in favour reaches `be_at` (price), the stop
                     goes to entry + `be_offset`
          trail      once the move reaches `trail_start`, the stop follows the
                     best price at `trail_dist`; it never loosens
          time stop  at entry + time_stop_s, exit if the trade is below
                     time_stop_min_r R; max_hold_s closes anything still open
  costs   spread is paid through bid/ask; commission (price units, round trip)
          is subtracted; gross = the same trade on MID prices, so
          cost share = (gross - net) / gross winners

Account rules applied in time order, as live: one position (or one basket -
legs sharing `group`) per strategy at a time; a daily loss stop in R; a pause
after N losses in a row.
"""

from __future__ import annotations

import statistics as st
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import numpy as np

from backend.scalping.ticks import Ticks

UTC = timezone.utc


@dataclass
class Order:
    t_ms: int                       # decision time (UTC ms)
    side: int                       # +1 buy, -1 sell
    sl: float                       # stop price
    tp: float | None = None         # target price (None = none)
    kind: str = "MARKET"            # MARKET / LIMIT / STOP
    price: float | None = None      # LIMIT/STOP price; MARKET: the signal price (for max_slip)
    expire_s: float = 0.0           # LIMIT/STOP lifetime
    max_slip: float | None = None   # MARKET: abort if the fill is this much worse than `price`
    be_at: float | None = None      # move in favour (price) that sets breakeven
    be_offset: float = 0.0
    trail_start: float | None = None
    trail_dist: float | None = None
    time_stop_s: float | None = None
    time_stop_min_r: float = 0.0
    max_hold_s: float = 6 * 3600
    group: int | None = None        # legs of one basket share a group id
    weight: float = 1.0             # share of the trade's risk carried by this leg
    tag: str = ""


@dataclass
class Fill:
    tag: str
    group: int | None
    side: int
    t_signal: int
    t_entry: int
    t_exit: int
    entry: float
    exit: float
    sl0: float
    reason: str
    r: float                        # net R (on this leg's own initial risk)
    r_gross: float                  # mid-price R, no spread / commission
    weight: float
    mfe_r: float
    mae_r: float


@dataclass
class Costs:
    latency_ms: int = 250
    commission: float = 0.0         # round trip, in PRICE units per 1 unit traded (= $ per lot / contract size)


def _first(mask: np.ndarray) -> int:
    """Index of the first True, or -1."""
    if mask.size == 0:
        return -1
    i = int(np.argmax(mask))
    return i if mask[i] else -1


def simulate(o: Order, tk: Ticks, costs: Costs) -> Fill | None:
    """One order on the ticks. None if it never filled (or was aborted)."""
    t0 = o.t_ms + costs.latency_ms
    i0 = int(np.searchsorted(tk.t, t0, "left"))
    if i0 >= len(tk):
        return None
    buy = o.side > 0
    # ── entry
    if o.kind == "MARKET":
        fi = i0
        entry = float(tk.ask[fi] if buy else tk.bid[fi])
        if o.price is not None and o.max_slip is not None and (entry - o.price) * o.side > o.max_slip:
            return None
    else:
        j = int(np.searchsorted(tk.t, t0 + int(o.expire_s * 1000), "right")) if o.expire_s > 0 else len(tk)
        q = tk.ask[i0:j] if buy else tk.bid[i0:j]
        if o.kind == "LIMIT":
            hit = _first(q <= o.price) if buy else _first(q >= o.price)
        else:
            hit = _first(q >= o.price) if buy else _first(q <= o.price)
        if hit < 0:
            return None
        fi = i0 + hit
        px = float(q[hit])
        if o.kind == "LIMIT":
            entry = min(px, o.price) if buy else max(px, o.price)
        else:
            entry = max(px, o.price) if buy else min(px, o.price)
    risk = (entry - o.sl) * o.side
    if risk <= 0:
        return None                                   # stop already crossed at the fill
    # ── the trade's tick window
    k_end = int(np.searchsorted(tk.t, tk.t[fi] + int(o.max_hold_s * 1000), "right"))
    a = fi + 1 if o.kind == "MARKET" else fi          # a limit can stop out on its own fill tick
    a = min(a, k_end)
    exit_q = tk.bid[a:k_end] if buy else tk.ask[a:k_end]   # what we'd close at
    tt = tk.t[a:k_end]
    if exit_q.size == 0:
        return _done(o, tk, fi, fi, entry, entry, "no ticks", risk, costs)
    fav = (exit_q - entry) * o.side                   # move in favour at each tick (close side)
    best = np.maximum.accumulate(fav)
    # stop level per tick: initial, then breakeven, then trail - never loosening
    stop_dist = np.full(exit_q.size, -risk)           # stop as "fav" level: -risk = initial stop
    if o.be_at is not None:
        stop_dist = np.where(best >= o.be_at, np.maximum(stop_dist, o.be_offset), stop_dist)
    if o.trail_dist is not None:
        start = o.trail_start if o.trail_start is not None else 0.0
        trail = np.where(best >= start, best - o.trail_dist, -np.inf)
        stop_dist = np.maximum(stop_dist, trail)
    stop_dist = np.maximum.accumulate(stop_dist)
    # the stop set by the best price up to tick k acts from tick k+1
    act = np.r_[-risk, stop_dist[:-1]]
    hit_sl = _first(fav <= act)
    hit_tp = _first(fav >= (o.tp - entry) * o.side) if o.tp is not None else -1
    cands = []
    if hit_sl >= 0:
        lvl = act[hit_sl]
        if lvl <= -risk + 1e-12:
            reason = "SL"
        elif o.be_at is not None and abs(lvl - o.be_offset) < 1e-9:
            reason = "breakeven"
        else:
            reason = "trail"
        cands.append((hit_sl, reason, float(exit_q[hit_sl])))
    if hit_tp >= 0:
        cands.append((hit_tp, "TP", float(o.tp)))
    if o.time_stop_s:
        ti = int(np.searchsorted(tt, tk.t[fi] + int(o.time_stop_s * 1000), "left"))
        if ti < exit_q.size and fav[ti] / risk < o.time_stop_min_r:
            cands.append((ti, "time stop", float(exit_q[ti])))
    if cands:
        k, reason, px = min(cands, key=lambda c: (c[0], c[1] != "SL"))   # same tick: stop first
    else:
        k, reason, px = exit_q.size - 1, "max hold", float(exit_q[-1])
    xi = a + k
    out = _done(o, tk, fi, xi, entry, px, reason, risk, costs)
    seen = fav[:k + 1]
    out.mfe_r, out.mae_r = float(seen.max() / risk), float(-seen.min() / risk)
    return out


def _done(o, tk, fi, xi, entry, px, reason, risk, costs) -> Fill:
    net = ((px - entry) * o.side - costs.commission) / risk
    mid_in, mid_out = (tk.bid[fi] + tk.ask[fi]) / 2, (tk.bid[xi] + tk.ask[xi]) / 2
    gross = (mid_out - mid_in) * o.side / risk
    return Fill(o.tag, o.group, o.side, int(o.t_ms), int(tk.t[fi]), int(tk.t[xi]), float(entry), float(px), float(o.sl),
                reason, float(net), float(gross), o.weight, 0.0, 0.0)


@dataclass
class Rules:
    daily_loss_r: float | None = None     # stop for the day after losing this many R
    pause_after_losses: int = 0           # N losing trades/baskets in a row ...
    pause_s: float = 0.0                  # ... pauses new entries this long
    min_gap_s: float = 0.0                # between entries
    allow_overlap: bool = False           # more than one position/basket at a time
    day_start_hour: int = 0               # UTC hour the trading day starts (daily stop resets)


def run(orders: list[Order], tk: Ticks, costs: Costs | None = None, rules: Rules | None = None) -> list[Fill]:
    """All orders in time order under the account rules. Baskets: legs with the
    same group are taken together and count as one trade for the rules."""
    costs, rules = costs or Costs(), rules or Rules()
    orders = sorted(orders, key=lambda o: (o.t_ms, o.group or 0))
    fills: list[Fill] = []
    busy_until = -1
    last_entry = -10**15
    day_r: dict[int, float] = defaultdict(float)
    closed: list[tuple[int, float]] = []            # (exit time, R of trade/basket) - known only after exit
    losses_in_row, paused_until = 0, -1
    i = 0
    while i < len(orders):
        o = orders[i]
        legs = [o]
        if o.group is not None:
            while i + 1 < len(orders) and orders[i + 1].group == o.group:
                i += 1
                legs.append(orders[i])
        i += 1
        t = o.t_ms
        if not rules.allow_overlap and t < busy_until:
            continue
        if t - last_entry < rules.min_gap_s * 1000 or t < paused_until:
            continue
        # results of trades closed before t
        day = (t // 1000 - rules.day_start_hour * 3600) // 86400
        while closed and closed[0][0] <= t:
            xt, r = closed.pop(0)
            day_r[(xt // 1000 - rules.day_start_hour * 3600) // 86400] += r
            losses_in_row = losses_in_row + 1 if r < 0 else 0
            if rules.pause_after_losses and losses_in_row >= rules.pause_after_losses:
                paused_until = xt + int(rules.pause_s * 1000)
                losses_in_row = 0
        if t < paused_until:
            continue
        if rules.daily_loss_r is not None and day_r[day] <= -rules.daily_loss_r:
            continue
        got = [f for f in (simulate(leg, tk, costs) for leg in legs) if f is not None]
        if not got:
            continue
        fills += got
        last_entry = t
        end = max(f.t_exit for f in got)
        busy_until = max(busy_until, end)
        basket_r = sum(f.r * f.weight for f in got)
        closed.append((end, basket_r))
        closed.sort()
    return fills


# ── statistics ──────────────────────────────────────────────────────────────
def trades_of(fills: list[Fill]) -> list[dict]:
    """Baskets merged into one trade (R weighted by leg risk share)."""
    by: dict = defaultdict(list)
    for k, f in enumerate(fills):
        by[f.group if f.group is not None else f"_{k}"].append(f)
    out = []
    for legs in by.values():
        out.append({"t": min(f.t_entry for f in legs), "t_exit": max(f.t_exit for f in legs),
                    "side": legs[0].side, "tag": legs[0].tag,
                    "r": sum(f.r * f.weight for f in legs), "r_gross": sum(f.r_gross * f.weight for f in legs),
                    "legs": len(legs), "reason": legs[-1].reason,
                    "hold_s": (max(f.t_exit for f in legs) - min(f.t_entry for f in legs)) / 1000})
    return sorted(out, key=lambda x: x["t"])


def stats(fills: list[Fill], days: float | None = None) -> dict:
    tr = trades_of(fills)
    if not tr:
        return {"trades": 0}
    r = [x["r"] for x in tr]
    g = [x["r_gross"] for x in tr]
    wins = [x for x in r if x > 0.02]
    losses = [x for x in r if x < -0.02]
    eq = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(np.r_[0, eq])[1:] - eq)) if len(eq) else 0.0
    span_d = days or max(1.0, (tr[-1]["t"] - tr[0]["t"]) / 86_400_000)
    gross_win = sum(x for x in g if x > 0)
    month = defaultdict(float)
    for x in tr:
        month[datetime.fromtimestamp(x["t"] / 1000, UTC).strftime("%Y-%m")] += x["r"]
    streak = best = 0
    for x in r:
        streak = streak + 1 if x < -0.02 else 0
        best = max(best, streak)
    return {
        "trades": len(tr), "per_day": round(len(tr) / span_d, 2),
        "win_rate": round(len(wins) / len(tr), 3),
        "avg_r": round(st.mean(r), 3), "net_r": round(sum(r), 2),
        "pf": round(sum(wins) / -sum(losses), 2) if losses else None,
        "avg_win_r": round(st.mean(wins), 2) if wins else None,
        "avg_loss_r": round(st.mean(losses), 2) if losses else None,
        "max_dd_r": round(dd, 2), "max_losing_streak": best,
        "gross_r": round(sum(g), 2),
        "cost_share": round((sum(g) - sum(r)) / gross_win, 3) if gross_win > 0 else None,
        "median_hold_min": round(st.median(x["hold_s"] for x in tr) / 60, 1),
        "exits": dict(Counter(x["reason"] for x in tr)),
        "by_month_r": {k: round(v, 1) for k, v in sorted(month.items())},
    }


def split_stats(fills: list[Fill], cut_ms: int) -> dict:
    """Same statistics before and after a cut (tuning vs unseen period)."""
    a = [f for f in fills if f.t_signal < cut_ms]
    b = [f for f in fills if f.t_signal >= cut_ms]
    return {"tune": stats(a), "unseen": stats(b)}


def fill_dicts(fills: list[Fill]) -> list[dict]:
    return [asdict(f) for f in fills]
