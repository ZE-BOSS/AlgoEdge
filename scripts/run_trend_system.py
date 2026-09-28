#!/usr/bin/env python
"""
scripts/run_trend_system.py

A trend-following system, built properly — which every previous test in this
session was not.

THE MISTAKE I MADE
------------------
Every hypothesis I screened used a FIXED profit target (1R, 2R, 3R). For a
mean-reversion idea that is fine. For a trend system it destroys the thing being
measured: trend following makes its money in a small number of very large
winners, and capping them at 2R removes the entire right tail while keeping all
the losers. My "time-series momentum" test exited at a fixed horizon with a
fixed stop and unsurprisingly measured nothing.

WHAT A TREND SYSTEM ACTUALLY IS (Donchian / Turtle lineage, and every managed
futures fund since)
    entry     break of the N-day high (long) or low (short)
    initial   stop at `atr_stop` x ATR
    exit      a TRAILING stop — chandelier: `trail_atr` x ATR from the highest
              close since entry. No target. Winners run until the trend ends.
    sizing    volatility targeted: risk a constant fraction of equity per trade,
              which means smaller positions in loud markets (Moreira & Muir show
              vol scaling raises Sharpe materially)
    universe  every market available, because the tail arrives in whichever one
              happens to trend, and you cannot know which in advance

Expected shape: win rate 30-40%, average win 3-6x the average loss, long flat
stretches, and most of the year's profit from a handful of trades. If a run
shows a 50% win rate and small winners, the exit logic is wrong.

    python scripts/run_trend_system.py --feed fundednext
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
    DERIV_MARKETS, FEEDS, FN_MARKETS, load, summarise,
)

BARS_PER_DAY = 288


def donchian_trend(b: dict, sym: str, entry_days: int, exit_days: int,
                   atr_stop: float, trail_atr: float, slip: float,
                   allow_short: bool = True) -> list[dict]:
    h, lo, c, t = b["high"], b["low"], b["close"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    a = atr_daily(b)
    n = len(c)
    win = entry_days * BARS_PER_DAY
    ewin = exit_days * BARS_PER_DAY
    if n < win + 10:
        return []

    hi_n = np.full(n, np.nan)
    lo_n = np.full(n, np.nan)
    sw_h = np.lib.stride_tricks.sliding_window_view(h, win)
    sw_l = np.lib.stride_tricks.sliding_window_view(lo, win)
    hi_n[win:] = sw_h.max(axis=1)[:-1]      # excludes the current bar
    lo_n[win:] = sw_l.min(axis=1)[:-1]

    out: list[dict] = []
    i = win + 1
    while i < n - 2:
        if not np.isfinite(a[i]) or a[i] <= 0 or not np.isfinite(hi_n[i]):
            i += 1
            continue
        long_ = h[i] >= hi_n[i]
        short_ = allow_short and lo[i] <= lo_n[i]
        if not (long_ or short_):
            i += 1
            continue
        if long_ and short_:
            i += 1
            continue

        entry = hi_n[i] if long_ else lo_n[i]
        R = atr_stop * a[i]
        stop = entry - R if long_ else entry + R
        extreme = entry
        exit_px = None
        j = i + 1
        while j < n:
            if long_:
                extreme = max(extreme, c[j])
                trail = extreme - trail_atr * a[j]
                level = max(stop, trail)
                if lo[j] <= level:
                    exit_px = level
                    break
            else:
                extreme = min(extreme, c[j])
                trail = extreme + trail_atr * a[j]
                level = min(stop, trail)
                if h[j] >= level:
                    exit_px = level
                    break
            j += 1
        if exit_px is None:
            exit_px, j = c[n - 1], n - 1

        gross = (exit_px - entry) if long_ else (entry - exit_px)
        cost = spread[i] + sl
        out.append({"t": int(t[i]), "symbol": sym, "r": (gross - cost) / R,
                    "stop_distance": R, "bars": j - i})
        i = j + 1
    return out


def shape(trades: list[dict]) -> dict:
    r = np.array([x["r"] for x in trades])
    wins, losses = r[r > 0], r[r < 0]
    return {
        "avg_win_r": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "payoff": float(wins.mean() / abs(losses.mean())) if len(losses) and len(wins) else 0.0,
        "best_r": float(r.max()), "worst_r": float(r.min()),
        "top5_share": float(np.sort(r)[-5:].sum() / r.sum()) if r.sum() > 0 else float("nan"),
        "median_bars": float(np.median([x["bars"] for x in trades])),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="fundednext", choices=list(FEEDS))
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--slip-points", type=float, default=1.0)
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--entry", type=int, default=20)
    ap.add_argument("--trail", type=float, default=3.5)
    ap.add_argument("--atr-stop", type=float, default=2.0)
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

    if args.sweep:
        print(f"\nPARAMETER SWEEP — {args.feed}, portfolio R/month "
              f"(entry x trail, ATR stop 2.0)")
        entries = [5, 10, 20, 40]
        trails = [1.5, 2.5, 3.5, 5.0]
        print(f"  {'entry':>7s} " + " ".join(f"{('trail' + str(tr)):>10s}" for tr in trails))
        for ed in entries:
            cells = []
            for tr in trails:
                allt = []
                for sym, b in bars.items():
                    allt += donchian_trend(b, sym, ed, ed, 2.0, tr, args.slip_points)
                s = summarise(allt, "x")
                cells.append(f"{s.get('r_per_month', 0):>+10.1f}" if s.get("n", 0) > 15
                             else f"{'-':>10s}")
            print(f"  {ed:>5d}d " + " ".join(cells))
        return

    ENTRY, TRAIL, ATR_STOP = args.entry, args.trail, args.atr_stop
    print(f"\nTREND SYSTEM — {args.feed}, {args.since} -> today | "
          f"{ENTRY}-day breakout, {ATR_STOP}xATR stop, {TRAIL}xATR chandelier trail, no target")
    hdr = (f"{'market':14s} {'N':>4s} {'expR':>7s} {'win%':>6s} {'avgW':>7s} {'avgL':>7s} "
           f"{'payoff':>7s} {'best':>7s} {'totR':>8s} {'R/mo':>7s}")
    print(hdr)
    print("-" * len(hdr))

    allt: list[dict] = []
    for sym, b in bars.items():
        tr = donchian_trend(b, sym, ENTRY, ENTRY, ATR_STOP, TRAIL, args.slip_points)
        if len(tr) < 5:
            continue
        allt += tr
        s = summarise(tr, sym)
        sh = shape(tr)
        if "expectancy_r" not in s:
            continue
        print(f"{sym:14s} {s['n']:>4d} {s['expectancy_r']:>+7.3f} {s['win_rate'] * 100:>5.1f}% "
              f"{sh['avg_win_r']:>+7.2f} {sh['avg_loss_r']:>+7.2f} {sh['payoff']:>7.2f} "
              f"{sh['best_r']:>+7.2f} {s['total_r']:>+8.1f} {s['r_per_month']:>+7.2f}")

    if not allt:
        print("no trades")
        return

    s = summarise(allt, "PORTFOLIO")
    sh = shape(allt)
    rep = eng.report(eng.run_account(allt, 10_000.0, args.risk), "TREND PORTFOLIO")
    print(f"\nPORTFOLIO: {s['n']} trades | exp {s['expectancy_r']:+.3f}R | win {s['win_rate'] * 100:.1f}% "
          f"| payoff {sh['payoff']:.2f} | {s['r_per_month']:+.1f} R/mo | t {s['t_stat']:+.2f}")
    print(f"  tail: best trade {sh['best_r']:+.1f}R, worst {sh['worst_r']:+.1f}R, "
          f"top 5 trades = {sh['top5_share'] * 100:.0f}% of total profit, "
          f"median hold {sh['median_bars'] / BARS_PER_DAY:.1f} days")
    eng.print_report(rep)
    print(eng.month_table(rep))

    p = ROOT / "data" / "zone_study" / f"trend_{args.feed}.json"
    p.write_text(json.dumps({"summary": s, "shape": sh, "money": rep,
                             "r_sequence": [x["r"] for x in sorted(allt, key=lambda y: y["t"])]},
                            indent=1, default=float))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
