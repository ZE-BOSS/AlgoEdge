"""Replay the user's backtest exits against real ticks to find the true fill.

The app books 96-97% of stop exits at EXACTLY the stop price. On Boom/Crash the
instrument's defining feature is a violent one-tick spike, so the price that
actually trades when a stop is breached is often well beyond it. This measures
the difference on the user's own trades rather than arguing about it.

Method, per trade:
  * take the booked stop_loss and exit_time
  * search ticks in a window around exit_time for the first tick that crosses the
    stop in the adverse direction
  * that tick is the realistic market-order fill
  * recompute P&L with it, keeping volume and everything else identical

Only stop-type exits are re-priced (SL / TRAIL_SL / BE_SL). Targets are limit
orders and fill at the limit, so they are left alone — which is the conservative
choice, since it cannot flatter the corrected result.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DBG = HERE.parents[1] / "debug" / "backtest"
TICKS = HERE / "ticks"

TICK_FILE = {
    "Crash 1000 Index": "CRASH1000", "Crash 500 Index": "CRASH500",
    "Boom 1000 Index": "BOOM1000", "Boom 500 Index": "BOOM500",
    "Step Index": "stpRNG", "Range Break 100 Index": "RB100",
    "Range Break 200 Index": "RB200", "Jump 25 Index": "JD25",
    "Jump 100 Index": "JD100",
}
STOP_EXITS = {"SL", "TRAIL_SL", "BE_SL"}
# Search window around the booked exit. The engine detects the stop on an M5 bar
# and books the exit at that bar. The realistic fill is the first tick INSIDE that
# bar that crossed the level, so the defensible window is the bar itself. A wide
# window risks catching an unrelated earlier crossing and overstating slippage,
# so BACK and FWD are separated and the result is checked for sensitivity.
# exit_time is the M5 bar OPEN, not the fill moment: measured against ticks, the
# first crossing of the booked stop sits at +98 s (p75) to +282 s (p99) after it.
# So the fill lives in [exit_time, exit_time + 300]. Searching backwards instead
# finds the trail level at some earlier price it had already visited, which is a
# different event entirely - that mistake made the first run of this script report
# 85-116%% losses that were not real.
BACK = 0
FWD = 310


def ticks_for(symbol: str):
    code = TICK_FILE.get(symbol)
    if not code:
        return None
    f = sorted(TICKS.glob(f"{code}_*d.npz"), key=lambda x: x.stat().st_size)
    if not f:
        return None
    z = np.load(f[-1], allow_pickle=True)
    return z["t"].astype(np.int64), z["p"].astype(float)


def replay(fn: str, limit: int | None = None) -> None:
    d = json.loads((DBG / fn).read_text(encoding="utf-8"))
    tr = sorted(d["trades"], key=lambda t: t.get("entry_time") or 0)
    symbol = tr[0]["symbol"]
    tk = ticks_for(symbol)
    if tk is None:
        print(f"{symbol}: no tick data")
        return
    T, P = tk
    if limit:
        tr = tr[:limit]

    booked_tot = 0.0
    real_tot = 0.0
    repriced = 0
    slips = []
    unmatched = 0

    for t in tr:
        pnl = float(t.get("pnl") or 0.0)
        booked_tot += pnl
        er = t.get("exit_reason")
        if er not in STOP_EXITS:
            real_tot += pnl
            continue
        xt = t.get("exit_time")
        sl = t.get("stop_loss")
        ep = t.get("entry_price")
        vol = t.get("volume")
        if not (xt and sl and ep and vol):
            real_tot += pnl
            continue
        long = str(t.get("direction", "")).upper() in ("BUY", "LONG")
        lo = np.searchsorted(T, int(xt) - BACK)
        hi = np.searchsorted(T, int(xt) + FWD)
        seg = P[lo:hi]
        if seg.size == 0:
            real_tot += pnl
            unmatched += 1
            continue
        hits = np.flatnonzero(seg <= sl) if long else np.flatnonzero(seg >= sl)
        if hits.size == 0:
            real_tot += pnl
            unmatched += 1
            continue
        fill = float(seg[hits[0]])
        # keep the same $/point implied by the booked trade
        booked_move = (float(t["exit_price"]) - ep) if long else (ep - float(t["exit_price"]))
        if abs(booked_move) < 1e-12:
            real_tot += pnl
            continue
        per_point = pnl / booked_move
        real_move = (fill - ep) if long else (ep - fill)
        real_pnl = per_point * real_move
        real_tot += real_pnl
        repriced += 1
        slips.append(real_pnl - pnl)

    init = float(d.get("initial_balance") or 10000)
    s = np.asarray(slips) if slips else np.array([0.0])
    print(f"\n=== {symbol} / {tr[0]['strategy_id']}  ({len(tr)} trades) ===")
    print(f"  stop exits repriced from ticks : {repriced}  (unmatched {unmatched})")
    print(f"  booked total P&L               : ${booked_tot:+,.0f}")
    print(f"  tick-fill total P&L            : ${real_tot:+,.0f}")
    print(f"  difference                     : ${real_tot - booked_tot:+,.0f}"
          f"   ({(real_tot - booked_tot) / abs(booked_tot) * 100 if booked_tot else 0:+.1f}%)")
    print(f"  mean slippage per repriced exit: ${s.mean():+,.2f}"
          f"   worst ${s.min():+,.2f}")
    print(f"  STATIC-equivalent return        : booked {booked_tot / init * 100:+.1f}%"
          f"   ->  tick-fill {real_tot / init * 100:+.1f}%")


if __name__ == "__main__":
    want = sys.argv[1:] or [
        "backtest_Crash_1000_Index_5acec554.json",
        "backtest_Boom_1000_Index_81f53f9e.json",
        "backtest_Crash_1000_Index_b8d8031f.json",
        "backtest_Range_Break_100_Index_657b57d4.json",
    ]
    for fn in want:
        replay(fn)
