#!/usr/bin/env python
"""
scripts/run_combined_book.py

Three weak edges, combined — because diversification is the only free lunch in
this business, and it is the one lever I had not pulled.

Each stream below is individually too small to matter. But they trade different
things at different times, so if their returns are close to uncorrelated the
combined Sharpe is roughly the root-sum-of-squares of the parts, not their
average. That is the only legitimate way I know to build a strong return stream
out of weak ones.

STREAMS (all on real CFDs, all costed, all measured earlier in this session)
  trend      20-day Donchian break, 2xATR stop, 1.5xATR chandelier trail, no
             target. +0.067R over 937 trades, t +2.50 — the only significant
             real-CFD edge found.
  session    the marketed "first 5-minute candle vs the 12 EMA" rule, restricted
             to the markets where it is actually positive (indices, gold, BTC).
             Its own claims do not reproduce, but on those markets it is weakly
             positive after costs.
  overnight  long the close-to-open session on equity indices. +0.036R a night.

The report is in DOLLARS, per stream and combined, with the monthly table,
because R-multiples have been hiding how small these numbers are.

    python scripts/run_combined_book.py --feed deriv --since 2024-01-22
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
from scripts.run_claimed_strategy import claimed  # noqa: E402
from scripts.run_edge_screen2 import h5_overnight  # noqa: E402
from scripts.run_published_strategies import FEEDS, load, summarise  # noqa: E402
from scripts.run_trend_system import donchian_trend  # noqa: E402

OUT = ROOT / "data" / "zone_study"

# both feeds' naming; whatever is missing is skipped
TREND_MARKETS = ["US Tech 100", "US SP 500", "Germany 40", "US30", "SPX500",
                 "XAUUSD", "XAGUSD", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY"]
SESSION_MARKETS = ["US Tech 100", "Germany 40", "US30", "SPX500", "XAUUSD", "BTCUSD"]
NIGHT_MARKETS = ["US Tech 100", "US SP 500", "Germany 40", "US30", "SPX500"]


def sharpe_daily(trades: list[dict], risk: float = 0.005) -> tuple[float, float]:
    by_day: dict[int, float] = {}
    for x in trades:
        d = int(x["t"] // 86400)
        by_day[d] = by_day.get(d, 0.0) + x["r"] * risk
    v = np.array(list(by_day.values()))
    if len(v) < 5 or v.std() == 0:
        return 0.0, 0.0
    return float(v.mean() / v.std() * np.sqrt(252)), float(v.std() * np.sqrt(252))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="deriv", choices=list(FEEDS))
    ap.add_argument("--since", default="2024-01-22")
    ap.add_argument("--slip-points", type=float, default=1.0)
    ap.add_argument("--risk", type=float, default=0.5)
    args = ap.parse_args()

    bars_dir, specs_path = FEEDS[args.feed]
    eng.SPECS = json.loads(specs_path.read_text())
    t0 = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())

    def get(sym):
        try:
            b = load(bars_dir, sym)
        except FileNotFoundError:
            return None      # this feed does not carry that symbol
        m = b["time"] >= t0
        b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
        return b if len(b["time"]) >= 2000 else None

    streams: dict[str, list[dict]] = {"trend": [], "session": [], "overnight": []}
    for sym in TREND_MARKETS:
        b = get(sym)
        if b is None:
            continue
        streams["trend"] += donchian_trend(b, sym, 20, 20, 2.0, 1.5, args.slip_points)
    for sym in SESSION_MARKETS:
        b = get(sym)
        if b is None:
            continue
        streams["session"] += claimed(b, sym, args.slip_points, trail_atr=1.0, stop_atr=1.0)
    for sym in NIGHT_MARKETS:
        b = get(sym)
        if b is None:
            continue
        streams["overnight"] += h5_overnight(b, sym, args.slip_points, True)

    print(f"\nCOMBINED BOOK — {args.feed}, {args.since} -> today, "
          f"${10_000:,} at {args.risk}% risk per trade\n")
    hdr = (f"{'stream':12s} {'N':>5s} {'expR':>7s} {'win%':>6s} {'t':>6s} {'Sharpe':>7s} "
           f"{'ann vol':>8s} {'$ end':>10s} {'return':>9s} {'maxDD':>7s}")
    print(hdr)
    print("-" * len(hdr))

    daily_series = {}
    for name, tr in streams.items():
        if len(tr) < 20:
            continue
        s = summarise(tr, name)
        sh, vol = sharpe_daily(tr, args.risk / 100)
        rep = eng.report(eng.run_account(tr, 10_000.0, args.risk), name)
        print(f"{name:12s} {s['n']:>5d} {s['expectancy_r']:>+7.3f} {s['win_rate'] * 100:>5.1f}% "
              f"{s['t_stat']:>+6.2f} {sh:>7.2f} {vol * 100:>7.1f}% {rep['final']:>10,.0f} "
              f"{rep['return_pct']:>+8.1f}% {rep['max_dd_pct']:>6.1f}%")
        by_day: dict[int, float] = {}
        for x in tr:
            d = int(x["t"] // 86400)
            by_day[d] = by_day.get(d, 0.0) + x["r"]
        daily_series[name] = by_day

    # correlation between the streams, which is what decides whether this works
    names = list(daily_series)
    if len(names) > 1:
        days = sorted(set().union(*[set(d) for d in daily_series.values()]))
        mat = np.array([[daily_series[n].get(d, 0.0) for d in days] for n in names])
        print(f"\ndaily return correlation ({len(days)} days)")
        print("            " + " ".join(f"{n:>10s}" for n in names))
        C = np.corrcoef(mat)
        for i, n in enumerate(names):
            print(f"  {n:10s}" + " ".join(f"{C[i][j]:>10.2f}" for j in range(len(names))))

    combined = [x for tr in streams.values() for x in tr]
    combined.sort(key=lambda x: x["t"])
    s = summarise(combined, "COMBINED")
    sh, vol = sharpe_daily(combined, args.risk / 100)
    rep = eng.report(eng.run_account(combined, 10_000.0, args.risk), "COMBINED BOOK")
    print(f"\n{'COMBINED':12s} {s['n']:>5d} {s['expectancy_r']:>+7.3f} "
          f"{s['win_rate'] * 100:>5.1f}% {s['t_stat']:>+6.2f} {sh:>7.2f} {vol * 100:>7.1f}% "
          f"{rep['final']:>10,.0f} {rep['return_pct']:>+8.1f}% {rep['max_dd_pct']:>6.1f}%")
    eng.print_report(rep)
    print(eng.month_table(rep))
    print(f"\n  R/month {s['r_per_month']:+.1f} | a one-month challenge needs about +20")

    (OUT / f"combined_{args.feed}.json").write_text(json.dumps(
        {"summary": s, "sharpe": sh, "ann_vol": vol, "money": rep,
         "r_sequence": [x["r"] for x in combined]}, indent=1, default=float))
    print(f"\n-> {OUT / f'combined_{args.feed}.json'}")


if __name__ == "__main__":
    main()
