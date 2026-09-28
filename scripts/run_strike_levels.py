#!/usr/bin/env python
"""
scripts/run_strike_levels.py

Do option STRIKE LEVELS move the underlying intraday? A day-trading test.

THE QUESTION, AS ASKED
----------------------
"When there was a call strike here, this was the movement it had on NASDAQ.
When there was a put strike here, this was the movement it had." Not the expiry
date -- the strikes themselves, day to day, and whether they can be scalped or
day-traded on NASDAQ, S&P, gold or BTC.

WHY THIS TEST NEEDS NO OPTIONS SUBSCRIPTION
-------------------------------------------
Option strikes are not scattered. They sit on a FIXED GRID that is public and
unchanging: SPX lists every 5 and 25 points, NDX every 25, DAX every 50, gold
futures every $5 and $10, Deribit BTC every $1,000 and $2,500. So "is price
affected by being near a strike" is answerable from price data alone, on years
of it, for nothing -- and if the grid does nothing, no amount of open-interest
weighting on top of a grid that does nothing will rescue it.

What a paid chain would add is WHICH strikes matter most (the ones with the open
interest). That refines this test; it cannot create an effect the grid does not
have. This runs first for that reason.

THE THREE HYPOTHESES
--------------------
  MAGNET    price is drawn to strikes, so a session closes nearer one than chance
  BARRIER   an approach to a strike reverses more often than it breaks
  BREAK     an approach to a strike breaks and RUNS more often than it reverses

The last two are opposites, and a day-trading strategy exists either way -- fade
the touch, or trade the break. What would kill the idea is neither.

THE CONTROL
-----------
Every test is run against a SHIFTED GRID of the same spacing, offset by half a
step. A real strike effect must beat a grid of levels that are not strikes but
are spaced identically, because "price reacts near round-ish numbers" is a
different (and already-tested, already-negative) claim.

    py -3.12 scripts/run_strike_levels.py
    py -3.12 scripts/run_strike_levels.py --strategy
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
from scripts.run_app_form_check import broker_utc_offset_hours, ny_sessions  # noqa: E402
from scripts.run_published_strategies import FEEDS, load, summarise  # noqa: E402

OUT = ROOT / "data" / "strike_levels"

# (symbol, strike spacings actually listed on that underlying's options)
MARKETS = [
    ("US Tech 100", [25.0, 50.0, 100.0]),     # NDX lists 25s; QQQ maps to ~50 NDX pts
    ("US SP 500", [5.0, 25.0, 50.0]),         # SPX lists 5s near the money, 25s out
    ("Germany 40", [50.0, 100.0]),            # DAX options, 50-point grid
    ("XAUUSD", [5.0, 10.0, 25.0]),            # COMEX gold, $5 and $10
    ("BTCUSD", [1000.0, 2500.0, 5000.0]),     # Deribit BTC
]


def nearest_strike(price: np.ndarray, step: float, offset: float = 0.0) -> np.ndarray:
    return np.round((price - offset) / step) * step + offset


def distance_to_grid(price: np.ndarray, step: float, offset: float = 0.0) -> np.ndarray:
    """How far price sits from the nearest grid level, as a fraction of a step.
    0 = exactly on a strike, 0.5 = exactly between two."""
    return np.abs(price - nearest_strike(price, step, offset)) / step


def load_m5(sym: str, since: str) -> dict | None:
    bars_dir, _ = FEEDS["deriv"]
    try:
        b = load(bars_dir, sym)
    except FileNotFoundError:
        return None
    t0 = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    m = b["time"] >= t0
    b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
    return b if len(b["time"]) >= 20_000 else None


# ── hypothesis 1: magnet ────────────────────────────────────────────────────
def magnet(b: dict, step: float, offset: float, sessions) -> dict | None:
    """Is the SESSION CLOSE nearer a grid level than a uniformly random price?

    A uniform price sits on average 0.25 of a step from the nearest level. Below
    that is a pin; above it is repulsion.
    """
    closes = np.array([b["close"][last] for _, last in sessions], dtype=float)
    if len(closes) < 40:
        return None
    d = distance_to_grid(closes, step, offset)
    # the standard deviation of a uniform on [0, 0.5] is 0.5/sqrt(12)
    se = (0.5 / np.sqrt(12)) / np.sqrt(len(d))
    return {"n": len(d), "mean_dist": float(d.mean()), "expected": 0.25,
            "t": float((d.mean() - 0.25) / se)}


# ── hypotheses 2 and 3: barrier vs break ───────────────────────────────────
def touches(b: dict, step: float, offset: float, sessions, near: float = 0.08,
            horizon: int = 12) -> list[dict]:
    """Every intraday APPROACH to a grid level, and what happened next.

    An approach is a bar that closes within `near` of a step of a level having
    been further away on the previous bar -- so each approach is counted once,
    on the bar it arrives. `horizon` bars later (12 x M5 = one hour), did price
    end up back the way it came (a fade) or beyond the level (a break)?
    """
    c, h, lo = b["close"], b["high"], b["low"]
    out = []
    for first, last in sessions:
        if last - first < horizon + 10:
            continue
        seg = slice(first, last + 1)
        price = c[seg]
        d = distance_to_grid(price, step, offset)
        level = nearest_strike(price, step, offset)
        n = len(price)
        for i in range(1, n - horizon):
            if not (d[i] <= near and d[i - 1] > near):
                continue
            approached_from_below = price[i - 1] < level[i]
            future = price[i + horizon]
            move = (future - price[i]) / step        # in strike-steps
            broke = (future > level[i]) if approached_from_below else (future < level[i])
            out.append({
                "t": int(b["time"][first + i]), "move": float(move),
                "broke": bool(broke), "from_below": bool(approached_from_below),
                # signed so that positive = price continued THROUGH the level
                "through": float(move if approached_from_below else -move),
            })
    return out


def summarise_touches(rows: list[dict]) -> dict | None:
    if len(rows) < 50:
        return None
    through = np.array([x["through"] for x in rows])
    broke = np.array([x["broke"] for x in rows], dtype=float)
    se = through.std(ddof=1) / np.sqrt(len(through)) if through.std() else 0.0
    return {
        "n": len(rows), "break_rate": float(broke.mean() * 100),
        "mean_through": float(through.mean()),
        "t": float(through.mean() / se) if se else 0.0,
    }


ROW = (f"  {'market / step':30s} {'n':>6s} {'dist':>7s} {'t(pin)':>8s} "
       f"{'touch n':>8s} {'break%':>7s} {'through':>8s} {'t':>7s}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-01-22")
    ap.add_argument("--near", type=float, default=0.08)
    ap.add_argument("--horizon", type=int, default=12)
    ap.add_argument("--strategy", action="store_true", help="also price the tradable version")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    print("\nOPTION STRIKE LEVELS -- do they move the underlying intraday?")
    print(f"M5 bars from {args.since}; an 'approach' closes within {args.near:g} of a step of a")
    print(f"level; the outcome is read {args.horizon} bars (~{args.horizon * 5} min) later.")
    print("Each market is run against a HALF-STEP SHIFTED grid as the control.\n")
    print(ROW)
    print("  " + "-" * (len(ROW) - 2))

    results: dict = {}
    for sym, steps in MARKETS:
        b = load_m5(sym, args.since)
        if b is None:
            print(f"  {sym:30s}   no data")
            continue
        off_h = broker_utc_offset_hours(b)
        sessions = ny_sessions(b, off_h)
        if len(sessions) < 40:
            print(f"  {sym:30s}   too few sessions")
            continue
        for step in steps:
            for label, offset in (("strikes", 0.0), ("control", step / 2)):
                mg = magnet(b, step, offset, sessions)
                tc = summarise_touches(touches(b, step, offset, sessions,
                                               args.near, args.horizon))
                tag = f"{sym} @{step:g} {label}"
                results[tag] = {"magnet": mg, "touch": tc}
                if mg is None or tc is None:
                    print(f"  {tag:30s}   too few")
                    continue
                star = "  <--" if abs(tc["t"]) >= 2.0 else ""
                print(f"  {tag:30s} {mg['n']:>6d} {mg['mean_dist']:>7.4f} {mg['t']:>+8.2f} "
                      f"{tc['n']:>8d} {tc['break_rate']:>6.1f}% {tc['mean_through']:>+8.4f} "
                      f"{tc['t']:>+7.2f}{star}")
        print()

    (OUT / "strike_levels.json").write_text(json.dumps(results, indent=1, default=float))
    print(f"-> {OUT / 'strike_levels.json'}")
    print("\n  dist    average distance from the nearest level at the session close, in steps.")
    print("          0.25 is what a uniformly random price gives. Below = pinned, above = repelled.")
    print("  break%  of approaches, how many ended the horizon beyond the level.")
    print("  through mean move THROUGH the level in steps; positive = continuation, negative = fade.")


if __name__ == "__main__":
    main()
