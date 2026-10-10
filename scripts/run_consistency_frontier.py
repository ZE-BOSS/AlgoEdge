#!/usr/bin/env python
"""
scripts/run_consistency_frontier.py

What does the most CONSISTENT real-market strategy look like, and what does
consistency cost?

WHY THIS SCRIPT EXISTS
----------------------
Every search in this repo has optimised expectancy. The complaint is a
different one: the win streaks are short, the losing streaks are long and the
monthly record is patchy. That is a fair objection to a trend system - 38%
wins and a 14-trade losing streak is the shape of the thing - but "find a
strategy with a high win rate" is not a wish, it is a dial, and the dial has
a price. This measures the price rather than asserting it.

The family searched is the one that produces high win rates: FADE an
extension, take a SMALL target, accept a WIDE stop. Win rate rises as the
target shrinks, and so does the size of the eventual loser. The question is
whether any point on that curve is both consistent AND profitable after
costs on a real market.

Reported for every configuration:
  * win rate and the longest losing streak
  * the share of calendar months that finished green
  * expectancy, profit factor, return and maximum drawdown in dollars

Sorted by consistency, not by return, so the frontier is visible.

    python scripts/run_consistency_frontier.py --feed deriv
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_crt_study import aggregate, buckets, drop_overlaps  # noqa: E402
from scripts.run_h4_lab import (MIN_STOP_SPREADS, SLIP_POINTS, atr_h4,  # noqa: E402
                                money, score, split_at)
from scripts.run_published_strategies import FEEDS, load  # noqa: E402

REAL_DERIV = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY",
              "BTCUSD", "US Tech 100", "US SP 500", "Germany 40"]
REAL_FN = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "BTCUSD",
           "SPX500", "US30"]
TFS = {"M15": 15, "H1": 60, "H4": 240}


def fade_trades(b: dict, bars: dict, sym: str, *, ma_len: int, ext_atr: float,
                target_r: float, stop_atr: float, max_hold: int) -> list[dict]:
    """Fade an extension from the mean. Small target, wide stop.

    This is the shape that produces a high win rate, and the reason it does
    is not an edge - it is the payoff ratio. A 0.3R target against a 2.5x ATR
    stop wins most of the time by construction, and loses eight times its
    average win when it loses. Whether that nets out is the measurement.
    """
    o, hi, lo, c = bars["open"], bars["high"], bars["low"], bars["close"]
    n = len(c)
    a = atr_h4(bars)
    ma = np.full(n, np.nan)
    if n > ma_len:
        ma[ma_len:] = np.lib.stride_tricks.sliding_window_view(
            c, ma_len).mean(axis=1)[:n - ma_len]
    spread = b["spread_pts"][bars["first_i"]] * b["point"]
    slip = SLIP_POINTS * b["point"]
    out: list[dict] = []

    i = ma_len + 20
    while i < n - 2:
        if not np.isfinite(a[i]) or a[i] <= 0 or not np.isfinite(ma[i]):
            i += 1
            continue
        stretch = (c[i] - ma[i]) / a[i]
        short_ = stretch >= ext_atr
        long_ = stretch <= -ext_atr
        if long_ == short_:
            i += 1
            continue
        ei = i + 1
        entry = o[ei]
        R = stop_atr * a[i]
        if R <= 0 or R < MIN_STOP_SPREADS * spread[ei]:
            i += 1
            continue
        stop = entry - R if long_ else entry + R
        tgt = entry + target_r * R if long_ else entry - target_r * R
        end = min(i + max_hold, n - 1)
        gross, j = None, ei
        while j <= end:
            if long_:
                if lo[j] <= stop:
                    gross = stop - entry
                    break
                if hi[j] >= tgt:
                    gross = tgt - entry
                    break
            else:
                if hi[j] >= stop:
                    gross = entry - stop
                    break
                if lo[j] <= tgt:
                    gross = entry - tgt
                    break
            j += 1
        if gross is None:
            gross = (c[end] - entry) if long_ else (entry - c[end])
            j = end
        out.append({"t": int(bars["t"][ei]), "symbol": sym,
                    "r": (gross - spread[ei] - slip) / R,
                    "stop_distance": R, "exit_t": int(bars["t"][j])})
        i = j + 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="deriv", choices=list(FEEDS))
    ap.add_argument("--split", default="2025-04-01")
    args = ap.parse_args()

    bars_dir, specs_path = FEEDS[args.feed]
    eng.SPECS = json.loads(Path(specs_path).read_text(encoding="utf-8"))
    cut = int(datetime.fromisoformat(args.split)
              .replace(tzinfo=timezone.utc).timestamp())
    names = REAL_DERIV if args.feed == "deriv" else REAL_FN
    names = [m for m in names
             if (bars_dir / f"{m.replace(' ', '_')}.npz").exists() and m in eng.SPECS]

    book: dict[tuple[str, str], tuple] = {}
    for sym in names:
        raw = load(bars_dir, sym)
        for tf, mins in TFS.items():
            agg = aggregate(raw, buckets(raw["time"], mins))
            if len(agg["close"]) < 1200:
                continue
            book[(sym, tf)] = (raw, agg)
    print(f"[{args.feed}] {len(names)} real markets x {len(TFS)} timeframes")

    grid = list(itertools.product([20, 50], [1.5, 2.5], [0.25, 0.5, 1.0, 2.0],
                                  [1.5, 3.0], [12, 48]))
    rows = []
    for tf in TFS:
        for ma, ext, tgt, stp, hold in grid:
            ins, allt = [], []
            for (sym, t_), (raw, agg) in book.items():
                if t_ != tf:
                    continue
                tt = drop_overlaps(fade_trades(raw, agg, sym, ma_len=ma,
                                               ext_atr=ext, target_r=tgt,
                                               stop_atr=stp, max_hold=hold))
                allt += tt
                ins += split_at(tt, cut)[0]
            si = score(ins)
            if si["thin"] or si["n"] < 200:
                continue
            m = money(allt, 1.0)
            if not m:
                continue
            rows.append({
                "tf": tf, "ma": ma, "ext": ext, "target_r": tgt, "stop_atr": stp,
                "hold": hold, "n": m["trades"],
                "win_rate": m["win_rate"], "loss_streak": m["longest_loss_streak"],
                "win_streak": m["longest_win_streak"],
                "months_green": m["month_pos_pct"], "pf": m["profit_factor"],
                "profit_usd": m["profit_usd"], "dd_pct": m["max_dd_pct"],
                "exp_r": score(allt)["expectancy_r"],
            })

    rows.sort(key=lambda r: (-r["win_rate"]))
    print(f"\n{len(rows)} configurations, sorted by WIN RATE "
          f"(the thing being asked for):\n")
    print(f"{'tf':<4s} {'tgt':>5s} {'stop':>5s} {'hold':>5s} {'n':>6s} {'win%':>6s} "
          f"{'loss streak':>12s} {'months green':>13s} {'PF':>5s} "
          f"{'profit $':>10s} {'DD%':>6s} {'exp R':>8s}")
    for r in rows[:18]:
        print(f"{r['tf']:<4s} {r['target_r']:>5.2f} {r['stop_atr']:>5.1f} "
              f"{r['hold']:>5d} {r['n']:>6d} {r['win_rate']:>5.1f}% "
              f"{r['loss_streak']:>12d} {r['months_green']:>12.0f}% "
              f"{r['pf']:>5.2f} {r['profit_usd']:>+10,.0f} {r['dd_pct']:>5.1f}% "
              f"{r['exp_r']:>+8.4f}")

    prof = [r for r in rows if r["profit_usd"] > 0]
    print(f"\n  configurations that made money at all: {len(prof)} of {len(rows)}")
    if prof:
        prof.sort(key=lambda r: -r["win_rate"])
        b = prof[0]
        print(f"  highest win rate among the profitable ones: {b['win_rate']:.1f}% "
              f"({b['tf']}, target {b['target_r']}R, stop {b['stop_atr']}xATR) "
              f"-> {b['profit_usd']:+,.0f} over {b['n']} trades, "
              f"losing streak {b['loss_streak']}, months green {b['months_green']:.0f}%")

    out = ROOT / "debug" / f"consistency_{args.feed}.json"
    out.write_text(json.dumps(rows, indent=1, default=float), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
