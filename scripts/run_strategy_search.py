#!/usr/bin/env python
"""
scripts/run_strategy_search.py

Which strategy family made money on each market over the last 8 months, with its
settings chosen only from the period BEFORE those 8 months. Needs a connected MT5.

    python scripts/run_strategy_search.py
    python scripts/run_strategy_search.py --markets XAUUSD "US Tech 100" --oos-months 6

Writes data/strategy_search/<timestamp>.json and prints one table per market.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics.strategy_search import FAMILIES, Bars, SizingSpec, evaluate_family  # noqa: E402

DEFAULT_MARKETS = ["XAUUSD", "US Tech 100", "US SP 500", "XAGUSD", "XPTUSD", "EURUSD", "GBPJPY", "BTCUSD"]
OUT_DIR = ROOT / "data" / "strategy_search"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="+", default=DEFAULT_MARKETS)
    ap.add_argument("--start", default="2024-01-22", help="in-sample start (indices have no MT5 data before this)")
    ap.add_argument("--oos-months", type=int, default=8)
    ap.add_argument("--families", nargs="+", default=None)
    args = ap.parse_args()

    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 is not available: {mt5.last_error()}")
    tfs = {"M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1, "D1": mt5.TIMEFRAME_D1}
    now = datetime.now(timezone.utc)
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    oos_start = now - timedelta(days=round(30.44 * args.oos_months))
    families = [f for f in FAMILIES if not args.families or f.name in args.families]

    report = {"generated": now.isoformat(), "in_sample": [start.date().isoformat(), oos_start.date().isoformat()],
              "out_of_sample": [oos_start.date().isoformat(), now.date().isoformat()],
              "balance": 350.0, "risk_pct": 1.0, "markets": {}}
    for sym in args.markets:
        info = mt5.symbol_info(sym)
        if info is None or not mt5.symbol_select(sym, True):
            print(f"\n{sym}: not available on this account")
            continue
        spec = SizingSpec(info.trade_tick_value / info.trade_tick_size, info.volume_min) \
            if info.trade_tick_size else None
        cache: dict[str, Bars] = {}
        rows = []
        for fam in families:
            if fam.timeframe not in cache:
                # D1 families need their lookback before the in-sample window starts
                pad = timedelta(days=200) if fam.timeframe == "D1" else timedelta(days=0)
                rates = mt5.copy_rates_range(sym, tfs[fam.timeframe], start - pad, now)
                if rates is None or len(rates) < 300:
                    cache[fam.timeframe] = None
                else:
                    cache[fam.timeframe] = Bars.from_rates(sym, fam.timeframe, rates, info.point)
            b = cache[fam.timeframe]
            if b is None:
                rows.append({"family": fam.name, "timeframe": fam.timeframe, "verdict": "NO DATA"})
                continue
            rows.append(evaluate_family(b, fam, oos_start=int(oos_start.timestamp()), spec=spec))
        report["markets"][sym] = rows
        _print_market(sym, rows)

    mt5.shutdown()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"search_{now.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nfull report: {path}")
    return 0


def _print_market(sym: str, rows: list[dict]) -> None:
    print(f"\n{sym}")
    print(f"  {'family':<13}{'tf':<5}{'OOS trades':>11}{'win%':>7}{'avg R':>8}{'total R':>9}"
          f"{'$350 ->':>10}{'maxDD%':>8}{'+months':>9}  {'IS avg R':>8}  verdict")
    for r in rows:
        o, i = r.get("out_of_sample"), r.get("in_sample")
        if not o:
            print(f"  {r['family']:<13}{r['timeframe']:<5}{'':>71}  {r['verdict']}")
            continue
        wr = f"{100 * o['win_rate']:.0f}" if o["win_rate"] is not None else "-"
        ar = f"{o['avg_r']:+.2f}" if o["avg_r"] is not None else "-"
        print(f"  {r['family']:<13}{r['timeframe']:<5}{o['n']:>11}{wr:>7}{ar:>8}{o['total_r']:>+9.1f}"
              f"{o['final_balance']:>10.0f}{o['max_dd_pct']:>8.1f}{o['positive_months']:>5}/{o['months']:<3}"
              f"  {i['avg_r']:>+8.2f}  {r['verdict']}")


if __name__ == "__main__":
    raise SystemExit(main())
