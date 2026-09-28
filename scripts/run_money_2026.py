#!/usr/bin/env python
"""
scripts/run_money_2026.py

$10,000, 1 January 2026 to today, in dollars — for the three ideas on the table:

  round      round-number continuation (EURUSD 0.01 / XAUUSD 100 / BTCUSD 1000)
  synth      the same trade on Crash/Boom, the "trap" from the last study: the
             control earns the same, but the question here is whether it is
             PROFITABLE, not whether it is a zone effect
  prior      prior-zone retest — fade the retest of a level price reversed at
             before, with a placebo-level arm for comparison

Every trade is sized off the live account balance, rounded to the broker's lot
step, floored at the broker's minimum lot, and charged the bar's own spread plus
slippage. A trade the account cannot size responsibly is skipped and counted.

    python scripts/run_money_2026.py --balance 10000 --risk 0.5
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

from scripts.run_zone_robust import sequential_trades  # noqa: E402
from scripts.run_zone_study import CONTROL_OFFSETS, OUT, load  # noqa: E402
from scripts.run_prior_zone_study import atr, pivots, retests  # noqa: E402
from scripts.zone_money_engine import month_table, print_report, report, run_account  # noqa: E402

ROUND_BOOK = [("EURUSD", 0.01), ("XAUUSD", 100.0), ("BTCUSD", 1000.0)]
SYNTH_BOOK = [("Crash 1000 Index", 50.0), ("Boom 1000 Index", 100.0), ("Boom 900 Index", 100.0)]
PRIOR_BOOK = ["EURUSD", "XAUUSD", "BTCUSD"]

SINCE = "2026-01-01"


def clip(b: dict, since: str) -> dict:
    t0 = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    m = b["time"] >= t0
    return {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}


def round_trades(book, since, slip_points=1.0, offset=0.0) -> list[dict]:
    out = []
    for sym, step in book:
        b = clip(load(sym), since)
        for tr in sequential_trades(b, step, offset, approach=12, horizon=48,
                                    stop_frac=0.25, slip_points=slip_points):
            out.append({"t": tr["t"], "symbol": sym, "r": tr["r"],
                        "stop_distance": 0.25 * step})
    return out


def prior_trades(markets, since, placebo: bool, slip_points=1.0, fade: bool = True) -> list[dict]:
    """Fade the retest. Entry is the CLOSE of the retest bar — always executable,
    unlike assuming a fill at the zone price itself."""
    out = []
    for sym in markets:
        b = clip(load(sym), since)
        a = atr(b)
        hi_p, lo_p = pivots(b, 12)
        spread_px = b["spread_pts"] * b["point"]
        point = b["point"]

        levels = []
        for i in hi_p:
            born = int(i + 12)
            if np.isfinite(a[born]):
                levels.append((float(b["high"][i]) + (1.5 * a[born] if placebo else 0.0), born))
        for i in lo_p:
            born = int(i + 12)
            if np.isfinite(a[born]):
                levels.append((float(b["low"][i]) - (1.5 * a[born] if placebo else 0.0), born))

        ev = retests(b, a, levels, band=0.15, away=1.0, thresh=0.5,
                     horizon=48, max_tests=3)["events"]
        tmap = {int(t): k for k, t in enumerate(b["time"])}
        # ONE POSITION AT A TIME. Different zones fire on the same bar, and
        # letting them all trade counts the same move dozens of times over —
        # that is how this printed +54,000% on its first run.
        ev.sort(key=lambda e: e["t"])
        h, lo, c = b["high"], b["low"], b["close"]
        n = len(c)
        busy_until = -1
        for e in ev:
            if e["t"] < busy_until:
                continue
            i = tmap.get(e["t"])
            if i is None or i + 48 >= n:
                continue
            busy_until = e["t"] + 48 * 300

            # The trade is priced from the ENTRY, not from the zone. Booking a
            # race measured at the zone while entering at the bar's close is how
            # the first version reported 68% wins at 1:1: the target sat nearer
            # the entry than the stop did.
            entry = float(c[i])
            R = e["stop_distance"]
            short = e["approached_up"] if fade else (not e["approached_up"])
            if short:
                tgt, stp = entry - R, entry + R
                hit_t = np.flatnonzero(lo[i + 1:i + 49] <= tgt)
                hit_s = np.flatnonzero(h[i + 1:i + 49] >= stp)
            else:
                tgt, stp = entry + R, entry - R
                hit_t = np.flatnonzero(h[i + 1:i + 49] >= tgt)
                hit_s = np.flatnonzero(lo[i + 1:i + 49] <= stp)

            t0 = hit_t[0] if hit_t.size else 10 ** 9
            s0 = hit_s[0] if hit_s.size else 10 ** 9
            if t0 == s0 == 10 ** 9:
                exit_px = float(c[i + 48])
                gross = (entry - exit_px) if short else (exit_px - entry)
            else:
                gross = R if t0 < s0 else -R

            cost = spread_px[i] + slip_points * point
            out.append({"t": e["t"], "symbol": sym, "r": (gross - cost) / R,
                        "stop_distance": R})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--slip-points", type=float, default=1.0)
    ap.add_argument("--books", nargs="*", default=["round", "synth", "prior", "prior_placebo"])
    args = ap.parse_args()

    sources = {
        "round": lambda: round_trades(ROUND_BOOK, args.since, args.slip_points),
        "round_control": lambda: round_trades(ROUND_BOOK, args.since, args.slip_points,
                                              offset=CONTROL_OFFSETS[0]),
        "synth": lambda: round_trades(SYNTH_BOOK, args.since, args.slip_points),
        "synth_control": lambda: round_trades(SYNTH_BOOK, args.since, args.slip_points,
                                              offset=CONTROL_OFFSETS[0]),
        "prior": lambda: prior_trades(PRIOR_BOOK, args.since, placebo=False,
                                      slip_points=args.slip_points),
        "prior_placebo": lambda: prior_trades(PRIOR_BOOK, args.since, placebo=True,
                                              slip_points=args.slip_points),
        "prior_break": lambda: prior_trades(PRIOR_BOOK, args.since, placebo=False,
                                            slip_points=args.slip_points, fade=False),
        "prior_break_placebo": lambda: prior_trades(PRIOR_BOOK, args.since, placebo=True,
                                                    slip_points=args.slip_points, fade=False),
    }

    print(f"\n${args.balance:,.0f} from {args.since} to today | risk {args.risk}% per trade, "
          f"compounding | costs = bar spread + {args.slip_points} points")

    out = {}
    for name in args.books:
        trades = sources[name]()
        res = run_account(trades, start_balance=args.balance, risk_pct=args.risk)
        rep = report(res, name)
        print_report(rep)
        if rep.get("trades"):
            print(month_table(rep))
        out[name] = rep

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"money_2026_risk{args.risk}.json"
    p.write_text(json.dumps(out, indent=1, default=float))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
