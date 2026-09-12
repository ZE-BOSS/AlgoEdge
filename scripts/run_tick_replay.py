#!/usr/bin/env python
"""
scripts/run_tick_replay.py

Would the bar simulation's ORB trades have filled the same way on real ticks?

Each break-mode setup in the window is re-executed on MT5 ticks the way the live
bot trades it: market entry at the first tick at/after the entry bar opens
(ASK for a buy, BID for a sell), stop and target re-anchored to that fill, the
stop triggered on the opposite side of the book and filled at the tick that
crossed it (so slippage is real), the target filled at its limit price, and the
session close at the first tick at/after the close. The report compares R per
trade with the bar simulation's R for the same setup.

    python scripts/run_tick_replay.py --symbol GBPJPY --session london --range 60 --rr 3
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics.orb_research import MAX_HOLD_BARS, TF, build_setups, variant_key  # noqa: E402
from backend.analytics.strategy_search import Bars  # noqa: E402


def replay(ticks, s, rr: float, session_close: bool) -> tuple[float, float] | None:
    t_ms = ticks["time_msc"]
    k0 = int(np.searchsorted(t_ms, s.t_entry * 1000, side="left"))
    if k0 >= len(ticks):
        return None
    d = s.direction
    fill = float(ticks["ask"][k0] if d > 0 else ticks["bid"][k0])
    stop, target = fill - d * s.stop_dist, fill + d * rr * s.stop_dist
    hold_end = (s.t_entry + MAX_HOLD_BARS * TF) * 1000
    close_ms = s.close_ts * 1000 if session_close else None
    bid, ask = ticks["bid"], ticks["ask"]
    for k in range(k0, len(ticks)):
        px = float(bid[k] if d > 0 else ask[k])          # the side a close executes on
        if (close_ms is not None and t_ms[k] >= close_ms) or t_ms[k] >= hold_end:
            return d * (px - fill) / s.stop_dist, fill - s.entry
        if (d > 0 and px <= stop) or (d < 0 and px >= stop):
            return d * (px - fill) / s.stop_dist, fill - s.entry
        if (d > 0 and px >= target) or (d < 0 and px <= target):
            return d * (target - fill) / s.stop_dist, fill - s.entry
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--session", default="london")
    ap.add_argument("--range", type=int, default=60)
    ap.add_argument("--rr", type=float, default=3.0)
    ap.add_argument("--hold", action="store_true", help="no session close")
    ap.add_argument("--start", default="2026-01-10")
    args = ap.parse_args()

    import MetaTrader5 as mt5
    mt5.initialize()
    info = mt5.symbol_info(args.symbol)
    rates = mt5.copy_rates_range(args.symbol, mt5.TIMEFRAME_M15, datetime(2025, 11, 1, tzinfo=timezone.utc),
                                 datetime.now(timezone.utc))
    b = Bars.from_rates(args.symbol, "M15", rates, info.point)
    start = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp())
    key = variant_key(args.rr, not args.hold, False)
    setups = [s for s in build_setups(b, args.session, args.range) if s.entry_mode == "break" and s.t_entry >= start]

    bar_r, tick_r, entry_slip = [], [], []
    for s in setups:
        t_from = datetime.fromtimestamp(s.t_entry - 60, tz=timezone.utc)
        t_to = datetime.fromtimestamp(max(s.outcomes[key][1] + 2 * TF, s.close_ts + TF), tz=timezone.utc)
        ticks = mt5.copy_ticks_range(args.symbol, t_from, t_to, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) < 10:
            continue
        got = replay(ticks, s, args.rr, not args.hold)
        if got is None:
            continue
        tick_r.append(got[0])
        entry_slip.append(got[1] * s.direction / s.stop_dist)
        bar_r.append(s.outcomes[key][0])
    mt5.shutdown()

    if not bar_r:
        print("no trades replayed")
        return 1
    diff = [t - b_ for t, b_ in zip(tick_r, bar_r)]
    same_sign = sum((t > 0) == (b_ > 0) for t, b_ in zip(tick_r, bar_r)) / len(bar_r)
    print(f"{args.symbol} ORB {args.session} {args.range}m {key}: {len(bar_r)} trades since {args.start}")
    print(f"  bar simulation : avg {statistics.mean(bar_r):+.3f}R  total {sum(bar_r):+.1f}R")
    print(f"  tick replay    : avg {statistics.mean(tick_r):+.3f}R  total {sum(tick_r):+.1f}R")
    print(f"  per-trade gap  : mean {statistics.mean(diff):+.3f}R  median {statistics.median(diff):+.3f}R  "
          f"same win/loss {100 * same_sign:.0f}%  corr {np.corrcoef(bar_r, tick_r)[0, 1]:.2f}")
    print(f"  entry fill vs bar open: mean {statistics.mean(entry_slip):+.3f}R (spread + move in the first tick)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
