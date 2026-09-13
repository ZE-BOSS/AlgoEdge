#!/usr/bin/env python
"""
scripts/run_vwap_research.py

VWAP, confluence by confluence, on every market — no restrictions.

For each market it builds the permissive candidate list once (every optional
confluence recorded rather than enforced), then:

  1. ABLATION — what each confluence adds, per market and pooled, over the full
     history: trades it admits, trades it blocks, and the expectancy of each.
  2. OPTIMISATION — the best exit variant and confluence set chosen on the older
     window only, reported unchanged on 2 years / 1 year / 6 months.

Writes data/vwap_research/<batch>.pkl (candidates + the chosen configurations)
for the account simulations, and prints the tables.

    python scripts/run_vwap_research.py --out vwap_b1.pkl --markets GBPJPY EURUSD
"""

from __future__ import annotations

import argparse
import itertools
import pickle
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics.strategy_search import Bars  # noqa: E402
from backend.analytics.vwap_research import (  # noqa: E402
    CONFLUENCES, EXIT_VARIANTS, VWAPConfig, ablate, apply_combination, build_candidates,
    in_window, score, stats, variant_key,
)

MARKETS = ["GBPJPY", "EURJPY", "USDJPY", "GBPUSD", "EURUSD", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
           "AUDJPY", "CADJPY", "GBPAUD", "EURGBP", "GBPCHF", "EURAUD",
           "XAUUSD", "XAGUSD", "XPTUSD", "BTCUSD", "ETHUSD",
           "US Tech 100", "US SP 500", "Wall Street 30", "Germany 40", "UK 100", "Japan 225"]
T = lambda y, m, d: int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())
# Chosen on everything before 2026-03-12, then reported on the three windows the
# user asked for. The 6-month window is entirely out of sample.
PICK = (T(2024, 3, 1), T(2026, 3, 12))
WINDOWS = {"2 years": (T(2024, 9, 12), T(2026, 9, 12)),
           "1 year": (T(2025, 9, 12), T(2026, 9, 12)),
           "6 months": (T(2026, 3, 12), T(2026, 9, 12))}
OUT = ROOT / "data" / "vwap_research"

# Confluence sets tried on the picking window: the live default, the stripped
# core, and every single-confluence addition to the core.
CORE = ("in_session_window",)
SETS: list[tuple[str, tuple[str, ...], bool]] = [
    ("live default", ("slope_aligned", "momentum_aligned", "inside_1sigma", "converging",
                      "in_session_window"), True),
    ("bare core", (), False),
    ("core+session", CORE, False),
]
for f in CONFLUENCES:
    if f not in CORE:
        SETS.append((f"core+{f}", CORE + (f,), False))
SETS += [("core+slope+momentum", CORE + ("slope_aligned", "momentum_aligned"), False),
         ("core+slope+converging", CORE + ("slope_aligned", "converging"), False),
         ("core+slope+momentum+first", CORE + ("slope_aligned", "momentum_aligned"), True)]


def fmt(s: dict) -> str:
    if not s["n"]:
        return f"{'-':>6}"
    return f"{s['n']:>5} {s['avg_r']:+.3f}R {s['pf'] or 0:>5.2f} {s['t']:>+5.1f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="+", default=MARKETS)
    ap.add_argument("--out", default="vwap_setups.pkl")
    ap.add_argument("--timeframe", default="M5", choices=["M5", "M15"])
    ap.add_argument("--setups", nargs="+", default=["pullback", "reversion"])
    args = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 not available: {mt5.last_error()}")
    tf = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15}[args.timeframe]

    saved, specs, pooled = {}, {}, {}
    for sym in args.markets:
        if not mt5.symbol_select(sym, True):
            print(f"\n{sym}: not on this account")
            continue
        info = mt5.symbol_info(sym)
        rates = mt5.copy_rates_range(sym, tf, datetime(2024, 2, 1, tzinfo=timezone.utc),
                                     datetime(2026, 9, 12, tzinfo=timezone.utc))
        if rates is None or len(rates) < 20_000:
            print(f"\n{sym}: not enough {args.timeframe} history ({0 if rates is None else len(rates)} bars)")
            continue
        b = Bars.from_rates(sym, args.timeframe, rates, info.point)
        specs[sym] = {"value_per_price_per_lot": info.trade_tick_value / info.trade_tick_size if info.trade_tick_size else 0.0,
                      "min_lot": info.volume_min, "lot_step": info.volume_step, "max_lot": info.volume_max}
        cands = build_candidates(b, VWAPConfig(setups=tuple(args.setups)))
        saved[sym] = cands
        pooled.setdefault("all", []).extend(cands)
        print(f"\n==== {sym}: {len(cands)} candidates "
              f"({datetime.fromtimestamp(int(b.time[0]), tz=timezone.utc).date()} -> "
              f"{datetime.fromtimestamp(int(b.time[-1]), tz=timezone.utc).date()}, {args.timeframe}) ====")

        # 1. what each confluence adds, at the strategy's own 1:2 target.
        # Per setup: the two have different gate sets and different geometry, so a
        # pooled table averages populations that cannot be compared.
        key = variant_key(2.0, True, False)
        for setup in args.setups:
            sub = [c for c in in_window(cands, *PICK) if c.setup == setup]
            if not sub:
                continue
            print(f"  -- {setup} ({len(sub)} candidates in the picking window) --")
            print(f"  confluence          admits          blocks         edge added")
            for row in ablate(sub, key, base=CORE):
                if not row["n_on"] and not row["n_blocked"]:
                    continue
                print(f"  {row['confluence']:<18} {row['n_on']:>5} {row['avg_r_on'] or 0:+.3f}R   "
                      f"{row['n_blocked']:>5} {row['avg_r_blocked'] or 0:+.3f}R   "
                      f"{row['edge_added'] if row['edge_added'] is not None else 0:+.3f}R")

        # 2. best (confluence set x exit) chosen on the picking window only
        best = None
        for (name, use, first_only), (t_, hc, be) in itertools.product(SETS, EXIT_VARIANTS):
            k = variant_key(t_, hc, be)
            sc = score(apply_combination(in_window(cands, *PICK), use=use, first_only=first_only), k, 60)
            if best is None or sc > best[0]:
                best = (sc, name, use, first_only, k)
        if best is None or best[0] == float("-inf"):
            print("  nothing with 60+ trades was profitable on the picking window")
            continue
        _, name, use, first_only, k = best
        print(f"  chosen on {datetime.fromtimestamp(PICK[0], tz=timezone.utc).date()}->"
              f"{datetime.fromtimestamp(PICK[1], tz=timezone.utc).date()}: {name} | exit {k}"
              f"{' | first per session' if first_only else ''}")
        print(f"  {'window':<10}{'n':>6} {'avgR':>7} {'PF':>6} {'t':>6}")
        for wname, (lo, hi) in WINDOWS.items():
            sub = apply_combination(in_window(cands, lo, hi), use=use, first_only=first_only)
            print(f"  {wname:<10}{fmt(stats(sub, k))}")
        saved[f"{sym}__pick"] = (name, use, first_only, k)
    mt5.shutdown()

    if pooled.get("all"):
        print(f"\n==== POOLED across {len(saved)} markets ({len(pooled['all'])} candidates) ====")
        key = variant_key(2.0, True, False)
        print(f"  confluence          admits          blocks         edge added")
        for row in ablate(in_window(pooled["all"], *PICK), key, base=CORE):
            if not row["n_on"] and not row["n_blocked"]:
                continue
            print(f"  {row['confluence']:<18} {row['n_on']:>5} {row['avg_r_on'] or 0:+.3f}R   "
                  f"{row['n_blocked']:>5} {row['avg_r_blocked'] or 0:+.3f}R   "
                  f"{row['edge_added'] if row['edge_added'] is not None else 0:+.3f}R")

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / args.out, "wb") as f:
        pickle.dump({"candidates": saved, "specs": specs,
                     "windows": {"pick": PICK, **WINDOWS}}, f)
    print(f"\nsaved {OUT / args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
