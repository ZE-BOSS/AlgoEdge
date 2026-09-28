#!/usr/bin/env python
"""
scripts/run_edge_screen2.py

Round two, and deliberately NOT more intraday patterns.

The first screen established why those fail: cost per trade dominates, and
widening the stop takes expectancy to zero rather than to profit. So this round
tests effects that do not need you to beat the spread many times a day.

H5  THE OVERNIGHT EFFECT
    Equity index returns accrue almost entirely while the cash market is SHUT.
    Measured on ETFs Q3-2020 to Q3-2025: SPY +47.1% close-to-open against +29.9%
    open-to-close; QQQ +53.5% vs +30.3%; for QQQ since 1999, 92.6% of all gains
    came overnight. Mechanism: risk transfer at the close, futures repricing on
    overnight news, and dealers unwilling to hold inventory through the gap.
    Trade: long the close-to-open session, flat during the day. Also tested in
    reverse (short the day session), which is the same claim.

H6  TIME-SERIES MOMENTUM
    Moskowitz, Ooi & Pedersen (2012): the sign of an instrument's own past
    return predicts its next return across 58 futures. The effect is real but
    McLean & Pontiff (2016) measure a 58% post-publication decay, so it is
    tested here at several lookbacks rather than assumed.

H7  TURN OF THE MONTH
    Equity index returns cluster around the month boundary (Xu & McConnell):
    pension inflows and infrequent rebalancing land on the same few days. Long
    from the last trading day of the month through the first three of the next.
    Four trades a month, which is the point — almost no spread paid.

CONTROL
    Every one of these is long-biased on instruments that mostly rose in 2026.
    So each is reported against BUY AND HOLD over the identical window. A
    strategy that makes money because it was long does not get credit for it.

    python scripts/run_edge_screen2.py --feed fundednext
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_edge_screen import atr as atr_m5  # noqa: E402


def atr_daily(b: dict, days: int = 14) -> np.ndarray:
    """True range of DAILY bars, carried back onto every M5 bar.

    The first version used the M5 ATR for multi-day stops. That is the average
    range of a FIVE MINUTE bar — a 2x multiple of it is stopped out within
    minutes, which is why tsmom showed a 5-11% win rate on a five-day hold and
    an expectancy of +1.8R: a handful of survivors running for days against a
    stop that was never survivable. The stop has to be on the scale of the hold.
    """
    day = (b["time"] // 86400).astype(int)
    edges = np.r_[0, np.flatnonzero(np.diff(day)) + 1]
    hi = np.maximum.reduceat(b["high"], edges)
    lo = np.minimum.reduceat(b["low"], edges)
    cl = b["close"][np.r_[edges[1:] - 1, len(b["close"]) - 1]]
    pc = np.r_[cl[0], cl[:-1]]
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
    smoothed = np.full(len(tr), np.nan)
    if len(tr) > days:
        cs = np.cumsum(tr)
        smoothed[days:] = (cs[days:] - cs[:-days]) / days
    out = np.full(len(b["time"]), np.nan)
    for k, start in enumerate(edges):
        end = edges[k + 1] if k + 1 < len(edges) else len(out)
        out[start:end] = smoothed[k]
    return out
from scripts.run_published_strategies import (  # noqa: E402
    DERIV_MARKETS, FEEDS, FN_MARKETS, load, session_open_minute, summarise,
)

INDICES = {"US30", "SPX500", "US Tech 100", "US SP 500", "Germany 40"}
CASH_MINUTES = 390          # a US cash session


def sessions_with_close(b: dict, length_min: int = CASH_MINUTES):
    """(open_idx, close_idx) per day, from the volume-detected cash open."""
    om = session_open_minute(b)
    mins = ((b["time"] % 86400) // 60).astype(int)
    day = ((b["time"] - om * 60) // 86400).astype(int)
    out = []
    for d in np.unique(day):
        idx = np.flatnonzero(day == d)
        if len(idx) < 10:
            continue
        rel = (mins[idx] - om) % 1440
        inside = idx[rel <= length_min]
        if len(inside) < 10:
            continue
        out.append((int(inside[0]), int(inside[-1])))
    return out


def h5_overnight(b: dict, sym: str, slip: float, hold_night: bool = True) -> list[dict]:
    """Long the close->open gap (hold_night) or short the open->close session."""
    c, o, t = b["close"], b["open"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    a = atr_daily(b)
    ses = sessions_with_close(b)
    out = []
    for k in range(len(ses) - 1):
        _, close_i = ses[k]
        next_open_i, next_close_i = ses[k + 1]
        if not np.isfinite(a[close_i]) or a[close_i] <= 0:
            continue
        R = 0.5 * a[close_i]
        if hold_night:
            entry, exit_px, entry_i = c[close_i], o[next_open_i], close_i
            gross = exit_px - entry
        else:
            entry, exit_px, entry_i = o[next_open_i], c[next_close_i], next_open_i
            gross = -(exit_px - entry)          # short the day session
        cost = spread[entry_i] + sl
        out.append({"t": int(t[entry_i]), "symbol": sym,
                    "r": (gross - cost) / R, "stop_distance": R})
    return out


def h6_tsmom(b: dict, sym: str, slip: float, look_days: int, hold_days: int,
             stop_atr: float = 2.0) -> list[dict]:
    c, h, lo, t = b["close"], b["high"], b["low"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    a = atr_daily(b)
    bars_day = 288
    look, hold = look_days * bars_day, hold_days * bars_day
    out, i = [], max(look, bars_day * 2)
    n = len(c)
    while i < n - hold - 1:
        if not np.isfinite(a[i]) or a[i] <= 0:
            i += bars_day
            continue
        past = c[i] - c[i - look]
        if past == 0:
            i += bars_day
            continue
        long_ = past > 0
        entry = c[i]
        R = stop_atr * a[i]
        stop = entry - R if long_ else entry + R
        end = min(i + hold, n - 1)
        gross = None
        for j in range(i + 1, end + 1):
            if long_ and lo[j] <= stop:
                gross = -R
                break
            if (not long_) and h[j] >= stop:
                gross = -R
                break
        if gross is None:
            gross = (c[end] - entry) if long_ else (entry - c[end])
        out.append({"t": int(t[i]), "symbol": sym,
                    "r": (gross - spread[i] - sl) / R, "stop_distance": R})
        i = end + 1
    return out


def h7_turn_of_month(b: dict, sym: str, slip: float, before: int = 1,
                     after: int = 3) -> list[dict]:
    c, t = b["close"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    a = atr_daily(b)
    days = np.unique((t // 86400).astype(int))
    day_of = (t // 86400).astype(int)
    last_idx = {d: int(np.flatnonzero(day_of == d)[-1]) for d in days}
    dates = {d: datetime.fromtimestamp(d * 86400, timezone.utc) for d in days}

    out = []
    for k, d in enumerate(days):
        nxt = days[k + 1] if k + 1 < len(days) else None
        if nxt is None or dates[nxt].month == dates[d].month:
            continue                      # d is the last trading day of its month
        entry_i = last_idx[days[max(0, k - (before - 1))]]
        exit_k = min(k + after, len(days) - 1)
        exit_i = last_idx[days[exit_k]]
        if not np.isfinite(a[entry_i]) or a[entry_i] <= 0 or exit_i <= entry_i:
            continue
        R = 2.0 * a[entry_i]
        gross = c[exit_i] - c[entry_i]
        out.append({"t": int(t[entry_i]), "symbol": sym,
                    "r": (gross - spread[entry_i] - sl) / R, "stop_distance": R})
    return out


def buy_and_hold(b: dict, sym: str) -> dict:
    """The control: what being long the whole window gave you, in the same R units."""
    a = atr_daily(b)
    i = int(np.argmax(np.isfinite(a)))
    R = 2.0 * a[i] if np.isfinite(a[i]) and a[i] > 0 else np.nan
    if not np.isfinite(R) or R <= 0:
        return {}
    gross = b["close"][-1] - b["close"][i]
    return {"symbol": sym, "r_total": gross / R,
            "pct": gross / b["close"][i] * 100}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="fundednext", choices=list(FEEDS))
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--slip-points", type=float, default=1.0)
    args = ap.parse_args()

    bars_dir, specs_path = FEEDS[args.feed]
    eng.SPECS = json.loads(specs_path.read_text())
    markets = FN_MARKETS if args.feed == "fundednext" else DERIV_MARKETS
    t0 = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())

    bars = {}
    for sym in markets:
        try:
            b = load(bars_dir, sym)
        except FileNotFoundError:
            continue
        m = b["time"] >= t0
        b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
        if len(b["time"]) >= 2000:
            bars[sym] = b

    print(f"\nEDGE SCREEN 2 — {args.feed}, {args.since} -> today, "
          f"costs = bar spread + {args.slip_points} pts")
    hdr = (f"{'market':14s} {'hypothesis':16s} {'N':>5s} {'expR':>7s} {'win%':>6s} "
           f"{'totR':>8s} {'t':>6s} {'/mo':>6s} {'R/mo':>7s}")
    print(hdr)
    print("-" * len(hdr))

    books: dict[str, list[dict]] = {}
    rows = []

    def add(sym, name, tr):
        s = summarise(tr, f"{sym}/{name}")
        if "expectancy_r" not in s:      # summarise returns a stub under 20 trades
            if s.get("n", 0):
                print(f"{sym:14s} {name:16s} {s['n']:>5d}   (too few to judge)")
            return
        books.setdefault(name, []).extend(tr)
        rows.append({"symbol": sym, "hypothesis": name, **s})
        print(f"{sym:14s} {name:16s} {s['n']:>5d} {s['expectancy_r']:>+7.3f} "
              f"{s['win_rate'] * 100:>5.1f}% {s['total_r']:>+8.1f} {s['t_stat']:>+6.2f} "
              f"{s['trades_per_month']:>6.1f} {s['r_per_month']:>+7.2f}")

    for sym, b in bars.items():
        if sym in INDICES:
            add(sym, "overnight_long", h5_overnight(b, sym, args.slip_points, True))
            add(sym, "day_short", h5_overnight(b, sym, args.slip_points, False))
            add(sym, "turn_of_month", h7_turn_of_month(b, sym, args.slip_points))
        for ld, hd in ((5, 5), (10, 10), (20, 20)):
            add(sym, f"tsmom_{ld}d", h6_tsmom(b, sym, args.slip_points, ld, hd))

    print("\nCONTROL — buy and hold over the same window")
    print(f"  {'market':14s} {'total R':>9s} {'price %':>9s}")
    for sym, b in bars.items():
        bh = buy_and_hold(b, sym)
        if bh:
            print(f"  {sym:14s} {bh['r_total']:>+9.1f} {bh['pct']:>+8.1f}%")

    print("\nPORTFOLIO PER HYPOTHESIS ($10,000 at 0.5% risk)")
    out = {"rows": rows, "books": {}}
    for name, tr in sorted(books.items()):
        s = summarise(tr, name)
        if s.get("n", 0) < 10:
            continue
        rep = eng.report(eng.run_account(tr, 10_000.0, 0.5), name)
        print(f"  {name:16s} {s['n']:>5d} trades | exp {s['expectancy_r']:>+6.3f}R "
              f"| {s['r_per_month']:>+6.1f} R/mo | ${rep['final']:>10,.0f} "
              f"({rep['return_pct']:>+7.1f}%) | DD {rep['max_dd_pct']:>4.1f}% | t {s['t_stat']:>+5.2f}")
        out["books"][name] = {"summary": s, "money": rep,
                              "r_sequence": [x["r"] for x in sorted(tr, key=lambda y: y["t"])]}

    p = ROOT / "data" / "zone_study" / f"edge_screen2_{args.feed}.json"
    p.write_text(json.dumps(out, indent=1, default=float))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
