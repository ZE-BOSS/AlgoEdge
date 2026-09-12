#!/usr/bin/env python
"""
scripts/run_orb_research.py

ORB across every available market: pick session / range / entry / exit on
2024-01-22..2026-01-10, pick one confluence the same way, then report both
unchanged on the last 8 months and on the 2022-09..2024-01 holdout.

Saves every setup (features + outcomes) to data/orb_research/setups.pkl so the
account simulations can reuse them without refetching.
"""

from __future__ import annotations

import argparse
import math
import pickle
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics.orb_research import (  # noqa: E402
    FEATURES, build_setups, choose_config, choose_filter, in_window, rs,
)
from backend.analytics.strategy_search import Bars  # noqa: E402

MARKETS = ["GBPJPY", "EURJPY", "USDJPY", "GBPUSD", "EURUSD", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
           "AUDJPY", "CADJPY", "GBPAUD", "EURGBP", "GBPCHF", "EURAUD",
           "XAUUSD", "XAGUSD", "XPTUSD", "BTCUSD", "ETHUSD",
           "US Tech 100", "US SP 500", "Wall Street 30", "Germany 40", "UK 100", "Japan 225"]
T = lambda y, m, d: int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())
EARLY, IS_, OOS = (T(2022, 9, 5), T(2024, 1, 22)), (T(2024, 1, 22), T(2026, 1, 10)), (T(2026, 1, 10), T(2026, 9, 12))
OUT = ROOT / "data" / "orb_research"


def stats(r: list[float]) -> str:
    if not r:
        return f"{'-':>5} {'':>7} {'':>5} {'':>5}"
    gw, gl = sum(x for x in r if x > 0), -sum(x for x in r if x < 0)
    t = statistics.mean(r) / (statistics.stdev(r) / math.sqrt(len(r))) if len(r) > 2 and statistics.stdev(r) > 0 else 0
    pf = gw / gl if gl > 0 else float("inf")
    return f"{len(r):>5} {statistics.mean(r):+.3f}R {pf:>5.2f} {t:>+5.1f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="+", default=MARKETS)
    ap.add_argument("--out", default="setups.pkl", help="file name under data/orb_research/ (one per parallel batch)")
    args = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 not available: {mt5.last_error()}")

    saved, specs = {}, {}
    print("window: pick on 2024-01-22..2026-01-10 | report last 8m (2026-01-10..) and 2022-09-05..2024-01-22")
    print("columns per period: n  avgR  PF  t")
    for sym in args.markets:
        if not mt5.symbol_select(sym, True):
            print(f"\n{sym}: not on this account")
            continue
        info = mt5.symbol_info(sym)
        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, datetime(2022, 6, 1, tzinfo=timezone.utc),
                                     datetime(2026, 9, 12, tzinfo=timezone.utc))
        if rates is None or len(rates) < 5000:
            print(f"\n{sym}: not enough M15 history")
            continue
        b = Bars.from_rates(sym, "M15", rates, info.point)
        specs[sym] = {"value_per_price_per_lot": info.trade_tick_value / info.trade_tick_size if info.trade_tick_size else 0.0,
                      "min_lot": info.volume_min, "lot_step": info.volume_step, "max_lot": info.volume_max}
        by_cfg = {}
        for session in ("london", "ny"):
            for rng in (15, 30, 60):
                for st in build_setups(b, session, rng):
                    by_cfg.setdefault((session, rng, st.entry_mode), []).append(st)
        saved[sym] = by_cfg
        pick = choose_config(by_cfg, *IS_)
        if not pick:
            print(f"\n{sym}: nothing profitable in-sample")
            continue
        cfg, key = pick
        setups = by_cfg[cfg]
        flt = choose_filter(setups, key, *IS_)
        print(f"\n{sym}: {cfg[0]} {cfg[1]}m {cfg[2]} {key}   (history from {datetime.fromtimestamp(int(b.time[0]), tz=timezone.utc).date()})")
        for label, sub in (("base", setups),
                           (f"+{flt[0]}={flt[1]}" if flt else "no filter", [s for s in setups if flt and s.features[flt[0]] is flt[1]])):
            if label == "no filter":
                print(f"   {label}")
                continue
            print(f"   {label:<24} early {stats(rs(in_window(sub, *EARLY), key))} | IS {stats(rs(in_window(sub, *IS_), key))} | last8m {stats(rs(in_window(sub, *OOS), key))}")
        saved[sym]["_pick"] = (cfg, key, flt)
    mt5.shutdown()

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / args.out, "wb") as f:
        pickle.dump({"setups": saved, "specs": specs, "windows": {"early": EARLY, "is": IS_, "oos": OOS}}, f)
    print(f"\nsaved {OUT / args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
