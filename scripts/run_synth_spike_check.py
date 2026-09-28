#!/usr/bin/env python
"""
scripts/run_synth_spike_check.py

The Crash/Boom result, re-priced with the fills those instruments actually give.

run_money_2026.py showed the level-continuation trade turning $10,000 into
$116,509 on Crash/Boom in nine months, with a 7.6% drawdown — and its control
doing slightly better, which already says it is nothing to do with levels.

There is a second, larger problem. A bar-level backtest fills a stop AT the
stop. On Boom and Crash every spike happens INSIDE a bar by construction, so a
stop on the spike side is taken out by a jump and fills far past it. The repo
measured this from 365 days of ticks (backend/backtester/fill_model.py):

    Crash 1000   n=2,190  mean overshoot 0.403 of the distance to the bar extreme
    Boom  1000   n=2,230  mean overshoot 0.353
    live fills, 4 stopped trades      mean +0.318 R of unbooked slippage
    the bar backtester                0.000 R

and charging it "turns +15.87 R over 132 trades into -5.58 R".

This script re-runs the same trades with that fill and nothing else changed.

    python scripts/run_synth_spike_check.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.backtester.fill_model import get_spike_fill  # noqa: E402
from scripts.run_zone_study import OUT, load, rolling_max, rolling_min  # noqa: E402
from scripts.zone_money_engine import print_report, report, run_account  # noqa: E402

BOOK = [("Crash 1000 Index", 50.0), ("Boom 1000 Index", 100.0), ("Boom 900 Index", 100.0)]
SINCE = "2026-01-01"


def trades(sym: str, step: float, since: str, spike_fill: bool, offset: float = 0.0,
           approach: int = 12, horizon: int = 48, stop_frac: float = 0.25,
           slip_points: float = 1.0) -> list[dict]:
    b = load(sym)
    t0 = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    m = b["time"] >= t0
    b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}

    high, low, close, t = b["high"], b["low"], b["close"], b["time"]
    spread_px = b["spread_pts"] * b["point"]
    n = len(high)
    R = stop_frac * step
    slip = slip_points * b["point"]

    spike = get_spike_fill(sym)
    side, lam = spike if spike else (0, 0.0)

    prev_max = rolling_max(high, approach)
    prev_min = rolling_min(low, approach)
    with np.errstate(invalid="ignore"):
        lvl_up = (np.floor(prev_max / step - offset) + 1 + offset) * step
        lvl_dn = (np.ceil(prev_min / step - offset) - 1 + offset) * step
    touch_up = np.isfinite(lvl_up) & (high >= lvl_up)
    touch_dn = np.isfinite(lvl_dn) & (low <= lvl_dn)

    out: list[dict] = []
    i = approach + 1
    while i < n - horizon - 1:
        if touch_up[i]:
            L, long_ = lvl_up[i], True
        elif touch_dn[i]:
            L, long_ = lvl_dn[i], False
        else:
            i += 1
            continue

        cost = spread_px[i] + slip
        h = high[i + 1: i + 1 + horizon]
        lo_ = low[i + 1: i + 1 + horizon]
        if long_:
            tgt = np.flatnonzero(h >= L + R)
            stp = np.flatnonzero(lo_ <= L - R)
        else:
            tgt = np.flatnonzero(lo_ <= L - R)
            stp = np.flatnonzero(h >= L + R)

        t0i = tgt[0] if tgt.size else 10 ** 9
        s0 = stp[0] if stp.size else 10 ** 9

        if t0i == s0 == 10 ** 9:
            held = horizon
            gross = (close[i + horizon] - L) if long_ else (L - close[i + horizon])
        elif t0i < s0:
            held, gross = int(t0i) + 1, R
        else:
            held = int(s0) + 1
            stop_level = (L - R) if long_ else (L + R)
            fill = stop_level
            if spike_fill and spike is not None:
                # side -1 = down spikes hurt LONG stops; +1 = up spikes hurt SHORT stops
                hits = (side == 2) or (side == -1 and long_) or (side == 1 and not long_)
                if hits:
                    bar = i + held
                    if long_:
                        fill = stop_level - lam * (stop_level - low[bar])
                        fill = max(fill, low[bar])
                    else:
                        fill = stop_level + lam * (high[bar] - stop_level)
                        fill = min(fill, high[bar])
            gross = (fill - L) if long_ else (L - fill)

        out.append({"t": int(t[i]), "symbol": sym, "r": (gross - cost) / R,
                    "stop_distance": R})
        i += held + 1
    return out


def main() -> None:
    print(f"\nCrash/Boom level-continuation, $10,000 at 0.5%, {SINCE} -> today")
    for sym, step in BOOK:
        sp = get_spike_fill(sym)
        print(f"  {sym}: spike profile {sp}")

    out = {}
    for label, spike, off in (("bar fill (stop fills AT the stop)", False, 0.0),
                              ("measured spike fill", True, 0.0),
                              ("CONTROL, non-round levels, spike fill", True, 0.17)):
        all_tr: list[dict] = []
        for sym, step in BOOK:
            all_tr += trades(sym, step, SINCE, spike_fill=spike, offset=off)
        res = run_account(all_tr, start_balance=10_000.0, risk_pct=0.5)
        rep = report(res, label)
        print_report(rep)
        out[label] = rep

    a = out["bar fill (stop fills AT the stop)"]
    b = out["measured spike fill"]
    print(f"\n  the fill assumption alone: ${a['final']:,.0f} -> ${b['final']:,.0f}")

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "synth_spike_check.json"
    p.write_text(json.dumps(out, indent=1, default=float))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
