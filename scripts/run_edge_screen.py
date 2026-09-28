#!/usr/bin/env python
"""
scripts/run_edge_screen.py

Four intraday hypotheses I believe in, screened across the assets a FundedNext
account can actually trade. No synthetics.

Each is chosen because there is a mechanism behind it, not because it backtests:

H1  COMPRESSION BREAKOUT
    Realised volatility is autocorrelated and mean-reverting: a quiet stretch is
    followed by a loud one more often than chance. Trade the break of a range
    that is unusually narrow FOR THAT MARKET, stop the other side, target 2R.
    Mechanism: market makers widen inventory bands after a squeeze resolves.

H2  OPENING DRIVE, ENTERED ON THE PULLBACK
    The session open carries the overnight order imbalance. The published ORB
    buys the break immediately and dies to spread (cost is 2-12% of a 5-minute
    R). Waiting for a pullback into the opening range puts the stop where it
    belongs and makes R several times larger, which is the whole game.

H3  PRIOR-DAY HIGH / LOW
    A real level: resting orders sit at yesterday's extreme because that is what
    every desk and retail platform draws. Unlike a round number it is a place
    trading actually happened. Trade the break, ATR stop, 2R target.

H4  LEAD-LAG
    Index CFDs are priced off the same futures complex. If SPX500 moves first,
    US30 follows within minutes. That is a genuine microstructure effect and it
    decays with liquidity, so it is worth measuring rather than assuming.

Every hypothesis is charged the bar's own spread plus slippage, is limited to
one position at a time per market, and reports R/month — because a prop
challenge is won on expectancy x frequency, not expectancy.

    python scripts/run_edge_screen.py --feed fundednext
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
from scripts.run_published_strategies import (  # noqa: E402
    DERIV_MARKETS, FEEDS, FN_MARKETS, day_index, load, session_open_minute, summarise,
)


def atr(b: dict, n: int = 288) -> np.ndarray:
    h, lo, c = b["high"], b["low"], b["close"]
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) > n:
        cs = np.cumsum(tr)
        out[n:] = (cs[n:] - cs[:-n]) / n
    return out


def _resolve(b: dict, i: int, entry: float, stop: float, tgt: float, long_: bool,
             horizon: int) -> tuple[float, int]:
    """(gross price move, bars held). Stop checked before target on the same bar."""
    h, lo, c = b["high"], b["low"], b["close"]
    end = min(i + 1 + horizon, len(h))
    for j in range(i + 1, end):
        if long_:
            if lo[j] <= stop:
                return stop - entry, j - i
            if h[j] >= tgt:
                return tgt - entry, j - i
        else:
            if h[j] >= stop:
                return entry - stop, j - i
            if lo[j] <= tgt:
                return entry - tgt, j - i
    last = end - 1
    return ((c[last] - entry) if long_ else (entry - c[last])), last - i


def h1_compression(b: dict, sym: str, slip: float, look: int = 24, pct: float = 25.0,
                   rr: float = 2.0, horizon: int = 96) -> list[dict]:
    h, lo, c, t = b["high"], b["low"], b["close"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    n = len(h)
    if n < look * 20:
        return []
    hi_roll = np.full(n, np.nan)
    lo_roll = np.full(n, np.nan)
    sw_h = np.lib.stride_tricks.sliding_window_view(h, look)
    sw_l = np.lib.stride_tricks.sliding_window_view(lo, look)
    hi_roll[look - 1:] = sw_h.max(axis=1)
    lo_roll[look - 1:] = sw_l.min(axis=1)
    width = hi_roll - lo_roll
    # "narrow for this market" measured against the trailing year of widths
    thresh = np.full(n, np.nan)
    win = 288 * 20
    for i in range(win, n, 288):
        thresh[i:i + 288] = np.nanpercentile(width[i - win:i], pct)

    out, i = [], win
    while i < n - horizon - 1:
        if not np.isfinite(width[i]) or not np.isfinite(thresh[i]) or width[i] > thresh[i]:
            i += 1
            continue
        hi, low_ = hi_roll[i], lo_roll[i]
        R = hi - low_
        if R <= 0:
            i += 1
            continue
        j = i + 1
        fired = False
        while j < min(i + 1 + horizon, n - 1):
            if h[j] >= hi:
                entry, stop, long_ = hi, low_, True
            elif lo[j] <= low_:
                entry, stop, long_ = low_, hi, False
            else:
                j += 1
                continue
            tgt = entry + rr * R if long_ else entry - rr * R
            gross, held = _resolve(b, j, entry, stop, tgt, long_, horizon)
            out.append({"t": int(t[j]), "symbol": sym,
                        "r": (gross - spread[j] - sl) / R, "stop_distance": R})
            i = j + held + 1
            fired = True
            break
        if not fired:
            i += horizon
    return out


def h2_opening_pullback(b: dict, sym: str, slip: float, or_bars: int = 6,
                        rr: float = 2.0, retrace: float = 0.5) -> list[dict]:
    om = session_open_minute(b)
    sessions = day_index(b, om)
    o, h, lo, c, t = b["open"], b["high"], b["low"], b["close"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    out = []
    for _, idx in sorted(sessions.items()):
        if len(idx) < or_bars + 12:
            continue
        rng = idx[:or_bars]
        hi, low_ = h[rng].max(), lo[rng].min()
        if hi <= low_:
            continue
        long_ = c[rng[-1]] > o[rng[0]]
        # wait for a pullback INTO the range, then trade the drive direction
        entry_px = hi - retrace * (hi - low_) if long_ else low_ + retrace * (hi - low_)
        stop = low_ if long_ else hi
        R = abs(entry_px - stop)
        if R <= 0:
            continue
        tgt = entry_px + rr * R if long_ else entry_px - rr * R
        rest = idx[or_bars:]
        hit = None
        for j in rest:
            if (long_ and lo[j] <= entry_px) or ((not long_) and h[j] >= entry_px):
                hit = j
                break
        if hit is None:
            continue
        gross, _ = _resolve(b, hit, entry_px, stop, tgt, long_, len(rest))
        out.append({"t": int(t[hit]), "symbol": sym,
                    "r": (gross - spread[hit] - sl) / R, "stop_distance": R})
    return out


def h3_prior_day(b: dict, sym: str, slip: float, rr: float = 2.0,
                 stop_atr: float = 0.5, horizon: int = 96) -> list[dict]:
    a = atr(b)
    day = (b["time"] // 86400).astype(int)
    h, lo, c, t = b["high"], b["low"], b["close"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    out = []
    days = np.unique(day)
    prior = {}
    for d in days:
        m = day == d
        prior[d] = (h[m].max(), lo[m].min())
    busy = -1
    for k in range(1, len(days)):
        d, pd_ = days[k], days[k - 1]
        ph, pl = prior[pd_]
        idx = np.flatnonzero(day == d)
        for j in idx:
            if j <= busy or not np.isfinite(a[j]) or j + horizon >= len(h):
                continue
            R = stop_atr * a[j]
            if R <= 0:
                continue
            if h[j] >= ph:
                entry, long_ = ph, True
            elif lo[j] <= pl:
                entry, long_ = pl, False
            else:
                continue
            stop = entry - R if long_ else entry + R
            tgt = entry + rr * R if long_ else entry - rr * R
            gross, held = _resolve(b, j, entry, stop, tgt, long_, horizon)
            out.append({"t": int(t[j]), "symbol": sym,
                        "r": (gross - spread[j] - sl) / R, "stop_distance": R})
            busy = j + held
            break
    return out


def h4_lead_lag(b_lead: dict, b_lag: dict, sym: str, slip: float, rr: float = 2.0,
                z: float = 2.0, horizon: int = 24) -> list[dict]:
    """Lead moves hard in one bar; take the lagger in the same direction."""
    tl = {int(x): i for i, x in enumerate(b_lead["time"])}
    c_lead, o_lead = b_lead["close"], b_lead["open"]
    ret_lead = np.zeros(len(c_lead))
    ret_lead[1:] = np.diff(c_lead) / np.maximum(c_lead[:-1], 1e-12)
    sd = np.full(len(ret_lead), np.nan)
    w = 288
    if len(ret_lead) > w:
        sw = np.lib.stride_tricks.sliding_window_view(ret_lead, w)
        sd[w:] = sw.std(axis=1)[:-1]

    a = atr(b_lag)
    h, lo, c, t = b_lag["high"], b_lag["low"], b_lag["close"], b_lag["time"]
    spread = b_lag["spread_pts"] * b_lag["point"]
    sl = slip * b_lag["point"]
    out, busy = [], -1
    for j in range(288, len(t) - horizon - 1):
        if j <= busy or not np.isfinite(a[j]):
            continue
        i = tl.get(int(t[j]))
        if i is None or not np.isfinite(sd[i]) or sd[i] <= 0:
            continue
        if abs(ret_lead[i]) < z * sd[i]:
            continue
        long_ = ret_lead[i] > 0
        entry = b_lag["open"][j + 1] if j + 1 < len(t) else c[j]
        R = 0.5 * a[j]
        if R <= 0:
            continue
        stop = entry - R if long_ else entry + R
        tgt = entry + rr * R if long_ else entry - rr * R
        gross, held = _resolve(b_lag, j + 1, entry, stop, tgt, long_, horizon)
        out.append({"t": int(t[j]), "symbol": sym,
                    "r": (gross - spread[j] - sl) / R, "stop_distance": R})
        busy = j + 1 + held
    return out


LEAD_LAG_PAIRS = {"fundednext": [("SPX500", "US30")],
                  "deriv": [("US SP 500", "US Tech 100"), ("US SP 500", "Germany 40")]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="fundednext", choices=list(FEEDS))
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--slip-points", type=float, default=1.0)
    ap.add_argument("--rr", type=float, default=2.0)
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

    print(f"\nEDGE SCREEN — {args.feed} feed, {args.since} -> today, target {args.rr}R, "
          f"costs = bar spread + {args.slip_points} pts")
    print(f"markets: {', '.join(bars)}")
    hdr = (f"{'market':14s} {'hypothesis':14s} {'N':>5s} {'expR':>7s} {'win%':>6s} "
           f"{'totR':>8s} {'t':>6s} {'/mo':>6s} {'R/mo':>7s}")
    print(hdr)
    print("-" * len(hdr))

    books: dict[str, list[dict]] = {}
    rows = []
    for sym, b in bars.items():
        cand = {
            "compression": lambda: h1_compression(b, sym, args.slip_points, rr=args.rr),
            "open_pullback": lambda: h2_opening_pullback(b, sym, args.slip_points, rr=args.rr),
            "prior_day": lambda: h3_prior_day(b, sym, args.slip_points, rr=args.rr),
        }
        for name, fn in cand.items():
            tr = fn()
            s = summarise(tr, f"{sym}/{name}")
            if s.get("n", 0) < 15:
                continue
            books.setdefault(name, []).extend(tr)
            rows.append({"symbol": sym, "hypothesis": name, **s})
            print(f"{sym:14s} {name:14s} {s['n']:>5d} {s['expectancy_r']:>+7.3f} "
                  f"{s['win_rate'] * 100:>5.1f}% {s['total_r']:>+8.1f} {s['t_stat']:>+6.2f} "
                  f"{s['trades_per_month']:>6.1f} {s['r_per_month']:>+7.2f}")

    for lead, lag in LEAD_LAG_PAIRS.get(args.feed, []):
        if lead in bars and lag in bars:
            tr = h4_lead_lag(bars[lead], bars[lag], lag, args.slip_points, rr=args.rr)
            s = summarise(tr, f"{lag}/lead_lag")
            if s.get("n", 0) >= 15:
                books.setdefault("lead_lag", []).extend(tr)
                rows.append({"symbol": f"{lead}->{lag}", "hypothesis": "lead_lag", **s})
                print(f"{lead + '->' + lag:14s} {'lead_lag':14s} {s['n']:>5d} "
                      f"{s['expectancy_r']:>+7.3f} {s['win_rate'] * 100:>5.1f}% "
                      f"{s['total_r']:>+8.1f} {s['t_stat']:>+6.2f} "
                      f"{s['trades_per_month']:>6.1f} {s['r_per_month']:>+7.2f}")

    print("\nPORTFOLIO PER HYPOTHESIS ($10,000 at 0.5% risk)")
    out = {"rows": rows, "books": {}}
    for name, tr in sorted(books.items()):
        s = summarise(tr, name)
        if s.get("n", 0) < 20:
            continue
        rep = eng.report(eng.run_account(tr, 10_000.0, 0.5), name)
        print(f"  {name:16s} {s['n']:>5d} trades | exp {s['expectancy_r']:>+6.3f}R "
              f"| {s['r_per_month']:>+6.1f} R/mo | ${rep['final']:>10,.0f} "
              f"({rep['return_pct']:>+7.1f}%) | DD {rep['max_dd_pct']:>4.1f}% | t {s['t_stat']:>+5.2f}")
        out["books"][name] = {"summary": s, "money": rep,
                              "r_sequence": [x["r"] for x in sorted(tr, key=lambda y: y["t"])]}

    combined = [x for tr in books.values() for x in tr]
    if combined:
        s = summarise(combined, "ALL")
        rep = eng.report(eng.run_account(combined, 10_000.0, 0.5), "ALL HYPOTHESES")
        print(f"\n  {'COMBINED':16s} {s['n']:>5d} trades | exp {s['expectancy_r']:>+6.3f}R "
              f"| {s['r_per_month']:>+6.1f} R/mo | ${rep['final']:>10,.0f} "
              f"({rep['return_pct']:>+7.1f}%) | DD {rep['max_dd_pct']:>4.1f}%")
        out["combined"] = {"summary": s, "money": rep,
                           "r_sequence": [x["r"] for x in sorted(combined, key=lambda y: y["t"])]}

    p = ROOT / "data" / "zone_study" / f"edge_screen_{args.feed}.json"
    p.write_text(json.dumps(out, indent=1, default=float))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
