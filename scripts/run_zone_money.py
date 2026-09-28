#!/usr/bin/env python
"""
scripts/run_zone_money.py

Turn the round-number asymmetry into R, with costs.

WHAT THE STATISTICS SAID
------------------------
run_zone_study.py, 5 years, M5: price touching a ROUND level continues through
it more often than price touching an equally spaced NON-round level. BTCUSD at
the 1,000 grid: 44.8% reversal vs 51.6% at control (z -10.6, N 6,997). EURUSD
and XAUUSD agree in sign; GBPJPY and US Tech 100 are flat.

That is the opposite of the folklore ("price bounces off round numbers") and it
is consistent with stop-loss clustering just beyond the level.

WHAT THIS SCRIPT ASKS
---------------------
Is it worth trading? The asymmetry is measured on a symmetric race of
±`stop_frac` * step, so the trade that harvests it directly is:

    enter   at the level, in the direction of the approach (continuation)
    stop    `stop_frac` * step back through the level
    target  `stop_frac` * step beyond it                     (1:1)

Costs are charged from the bar's own recorded spread plus a slippage
allowance, so an edge that only exists gross shows up as what it is.

The same trade is run on the control grid. The comparison is the point: a
result that is equally good at 1.10237 has nothing to do with round numbers.

    python scripts/run_zone_money.py --since 2026-01-01
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_zone_study import (  # noqa: E402
    CONTROL_OFFSETS, FX, SYNTH, OUT, load, pick_steps, rolling_max, rolling_min,
)


def trades(b: dict, step: float, offset: float, approach: int, horizon: int,
           stop_frac: float, slip_points: float, direction: str) -> list[dict]:
    """One trade per clean touch. `direction` is 'continuation' or 'reversal'."""
    high, low, close = b["high"], b["low"], b["close"]
    spread_px = b["spread_pts"] * b["point"]
    t = b["time"]
    n = len(high)
    if n < approach + horizon + 10:
        return []

    prev_max = rolling_max(high, approach)
    prev_min = rolling_min(low, approach)
    R = stop_frac * step
    slip = slip_points * b["point"]

    out: list[dict] = []
    with np.errstate(invalid="ignore"):
        lvl_up = (np.floor(prev_max / step - offset) + 1 + offset) * step
        lvl_dn = (np.ceil(prev_min / step - offset) - 1 + offset) * step

    for idx, lvl, approached_up in (
        (np.flatnonzero(np.isfinite(lvl_up) & (high >= lvl_up)), lvl_up, True),
        (np.flatnonzero(np.isfinite(lvl_dn) & (low <= lvl_dn)), lvl_dn, False),
    ):
        last = None
        last_i = -10 ** 9
        for i in idx:
            if i + horizon >= n:
                break
            L = lvl[i]
            if last is not None and abs(L - last) < 1e-12 and i - last_i < horizon:
                continue
            last, last_i = L, i

            # continuation = keep going the way price arrived
            long_ = approached_up if direction == "continuation" else (not approached_up)
            cost = spread_px[i] + slip                  # crossing the book + slippage
            h = high[i + 1: i + 1 + horizon]
            lo_ = low[i + 1: i + 1 + horizon]

            if long_:
                tgt = np.flatnonzero(h >= L + R)
                stp = np.flatnonzero(lo_ <= L - R)
            else:
                tgt = np.flatnonzero(lo_ <= L - R)
                stp = np.flatnonzero(h >= L + R)

            t0 = tgt[0] if tgt.size else 10 ** 9
            s0 = stp[0] if stp.size else 10 ** 9
            if t0 == s0 == 10 ** 9:
                # closed out at the horizon
                exit_px = close[i + horizon]
                gross = (exit_px - L) if long_ else (L - exit_px)
            elif t0 < s0:
                gross = R
            else:
                gross = -R
            out.append({"t": int(t[i]), "r": (gross - cost) / R, "win": gross > 0})
    return out


def summarise(tr: list[dict]) -> dict:
    if not tr:
        return {"n": 0}
    r = np.array([x["r"] for x in tr])
    wins = r > 0
    return {
        "n": int(len(r)),
        "expectancy_r": float(r.mean()),
        "total_r": float(r.sum()),
        "win_rate": float(wins.mean()),
        "t_stat": float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))) if len(r) > 2 and r.std() > 0 else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--approach", type=int, default=12)
    ap.add_argument("--horizon", type=int, default=48)
    ap.add_argument("--stop-frac", type=float, default=0.25)
    ap.add_argument("--slip-points", type=float, default=1.0)
    ap.add_argument("--direction", default="continuation", choices=["continuation", "reversal"])
    ap.add_argument("--markets", nargs="*", default=None)
    ap.add_argument("--tag", default="money")
    args = ap.parse_args()

    kw = dict(approach=args.approach, horizon=args.horizon, stop_frac=args.stop_frac,
              slip_points=args.slip_points, direction=args.direction)

    rows = []
    for sym in (args.markets or (FX + SYNTH)):
        try:
            b = load(sym)
        except FileNotFoundError as e:
            print(f"skip {e}")
            continue
        if args.since:
            t0 = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())
            m = b["time"] >= t0
            b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}

        for step in pick_steps(b):
            rnd = summarise(trades(b, step, 0.0, **kw))
            ctl_all: list[dict] = []
            for f in CONTROL_OFFSETS:
                ctl_all += trades(b, step, f, **kw)
            ctl = summarise(ctl_all)
            rows.append({"symbol": sym, "step": step, "round": rnd, "control": ctl})

    hdr = (f"{'market':22s} {'step':>9s} {'N':>6s} {'exp R':>8s} {'win%':>6s} {'total R':>9s} "
           f"{'t':>6s} | {'ctrl exp':>9s} {'ctrl win':>9s}")
    print(f"\n{args.direction} trade at the level | window {args.since or 'all'} | "
          f"1:1 at {args.stop_frac}xstep | costs: bar spread + {args.slip_points} points")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        a, c = r["round"], r["control"]
        if a.get("n", 0) < 30:
            continue
        print(f"{r['symbol']:22s} {r['step']:>9g} {a['n']:>6d} {a['expectancy_r']:>+8.3f} "
              f"{a['win_rate'] * 100:>5.1f}% {a['total_r']:>+9.1f} {a['t_stat']:>+6.2f} | "
              f"{c['expectancy_r']:>+9.3f} {c['win_rate'] * 100:>8.1f}%")

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"zone_money_{args.tag}.json"
    p.write_text(json.dumps({"params": vars(args), "rows": rows}, indent=1))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
