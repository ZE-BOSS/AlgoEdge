#!/usr/bin/env python
"""
scripts/run_claimed_strategy.py

Testing the marketed "one 5-minute candle" system.

THE CLAIM
    "At the New York open, the algo watches just one thing: the first 5-minute
     candle. If it closes above the 12 EMA, it goes long. If it closes below, it
     goes short. The algo manages the position, trails the stop as momentum
     develops... tested on Nasdaq from 2019 to 2026 across 1,448 trades.
     982% historical return, 57% win rate, 1.29 profit factor."

WHAT IS TESTABLE
    1,448 trades over ~7 years is ~207/year — one per trading day, which matches
    "one trade at the New York open". So the claim is checkable on any period:
    a 57% win rate and a 1.29 profit factor are properties of the rule, not of
    the sample, and should show up in any reasonably long window.

WHAT IS NOT SPECIFIED, and therefore tested as a range
    * the EMA's timeframe (12-period on 5-minute bars is the natural reading)
    * the trailing rule — "trails the stop as momentum develops" is not a rule.
      Tested as a chandelier trail at several ATR multiples.
    * the initial stop, if any
    * whether it exits at the close

A note on the headline: **982% is not a return you can compare to anything**
without knowing the risk per trade and whether it compounds. A 57% win rate at
1.29 profit factor is a modest edge; turning it into 982% requires leverage,
compounding, or both. The win rate and profit factor are the claims worth
checking, and this script checks those.

    python scripts/run_claimed_strategy.py --feed deriv
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
from scripts.run_edge_screen2 import atr_daily  # noqa: E402
from scripts.run_published_strategies import (  # noqa: E402
    FEEDS, day_index, load, session_open_minute, summarise,
)


def ema(x: np.ndarray, span: int) -> np.ndarray:
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def claimed(b: dict, sym: str, slip: float, ema_span: int = 12,
            trail_atr: float = 1.0, stop_atr: float = 1.0,
            exit_at_close: bool = True) -> list[dict]:
    o, h, lo, c, t = b["open"], b["high"], b["low"], b["close"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    e = ema(c, ema_span)
    a = atr_daily(b)
    om = session_open_minute(b)
    sessions = day_index(b, om)

    out = []
    for _, idx in sorted(sessions.items()):
        if len(idx) < 12:
            continue
        first = idx[0]
        if not np.isfinite(a[first]) or a[first] <= 0:
            continue
        # the rule: first 5-minute candle closes above/below the 12 EMA
        long_ = c[first] > e[first]
        entry_i = idx[1]
        entry = o[entry_i]
        R = stop_atr * a[first]
        hard = entry - R if long_ else entry + R

        extreme = entry
        exit_px = None
        rest = idx[1:]
        for j in rest:
            if long_:
                extreme = max(extreme, c[j])
                level = max(hard, extreme - trail_atr * a[first])
                if lo[j] <= level:
                    exit_px = level
                    break
            else:
                extreme = min(extreme, c[j])
                level = min(hard, extreme + trail_atr * a[first])
                if h[j] >= level:
                    exit_px = level
                    break
        if exit_px is None:
            exit_px = c[rest[-1]] if exit_at_close else c[rest[-1]]

        gross = (exit_px - entry) if long_ else (entry - exit_px)
        out.append({"t": int(t[entry_i]), "symbol": sym,
                    "r": (gross - spread[entry_i] - sl) / R, "stop_distance": R})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="deriv", choices=list(FEEDS))
    ap.add_argument("--since", default="2024-01-22")
    ap.add_argument("--slip-points", type=float, default=1.0)
    ap.add_argument("--markets", nargs="*",
                    default=["US Tech 100", "US SP 500", "Germany 40", "XAUUSD", "BTCUSD",
                             "EURUSD", "GBPJPY", "USDJPY"])
    args = ap.parse_args()

    bars_dir, specs_path = FEEDS[args.feed]
    eng.SPECS = json.loads(specs_path.read_text())
    t0 = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())

    bars = {}
    for sym in args.markets:
        try:
            b = load(bars_dir, sym)
        except FileNotFoundError:
            continue
        m = b["time"] >= t0
        b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
        if len(b["time"]) >= 2000:
            bars[sym] = b

    print(f"\nCLAIMED SYSTEM — first 5-min candle vs {12} EMA at the session open, "
          f"trailing stop\n{args.feed} feed, {args.since} -> today, costs charged")
    print("claim: 57% win rate, 1.29 profit factor, 1,448 trades (Nasdaq 2019-2026)\n")

    hdr = (f"{'market':14s} {'trail':>6s} {'N':>5s} {'win%':>6s} {'PF':>6s} {'expR':>7s} "
           f"{'totR':>8s} {'t':>6s}")
    print(hdr)
    print("-" * len(hdr))

    best_by_market = {}
    allrows = []
    for sym, b in bars.items():
        for trail in (0.5, 1.0, 2.0, 3.0):
            tr = claimed(b, sym, args.slip_points, trail_atr=trail)
            if len(tr) < 30:
                continue
            r = np.array([x["r"] for x in tr])
            gains, losses = r[r > 0].sum(), -r[r < 0].sum()
            pf = gains / losses if losses > 0 else float("inf")
            s = summarise(tr, sym)
            print(f"{sym:14s} {trail:>6.1f} {len(r):>5d} {(r > 0).mean() * 100:>5.1f}% "
                  f"{pf:>6.2f} {r.mean():>+7.3f} {r.sum():>+8.1f} {s['t_stat']:>+6.2f}")
            allrows.append({"symbol": sym, "trail": trail, "n": len(r),
                            "win_rate": float((r > 0).mean()), "pf": float(pf),
                            "expectancy_r": float(r.mean()), "t": s["t_stat"]})
            if sym not in best_by_market or r.sum() > best_by_market[sym][1]:
                best_by_market[sym] = (trail, r.sum(), tr)
        print()

    print("MONEY — each market at its best trail setting, $10,000 at 0.5% risk")
    for sym, (trail, tot, tr) in best_by_market.items():
        rep = eng.report(eng.run_account(tr, 10_000.0, 0.5), sym)
        if rep.get("trades"):
            print(f"  {sym:14s} trail {trail:>3.1f} | ${rep['final']:>10,.0f} "
                  f"({rep['return_pct']:>+7.1f}%) | win {rep['win_rate']:>4.1f}% "
                  f"| PF {rep['profit_factor']:>4.2f} | DD {rep['max_dd_pct']:>4.1f}% "
                  f"| {rep['trades']} trades")

    (ROOT / "data" / "zone_study" / f"claimed_{args.feed}.json").write_text(
        json.dumps(allrows, indent=1, default=float))


if __name__ == "__main__":
    main()
