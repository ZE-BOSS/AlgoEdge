#!/usr/bin/env python
"""
scripts/run_apa_research.py

APA, confluence by confluence, on every market — and the R:R question answered
properly: 1:1 through 1:10 plus the scale-out, on the same candidates.

Structure on M15, entries on M5, exactly as the live engine splits them.
Selection happens on the picking window only; the 2-year / 1-year / 6-month
columns are reported unchanged.

    python scripts/run_apa_research.py --out apa_b1.pkl --markets XAUUSD GBPJPY
"""

from __future__ import annotations

import argparse
import itertools
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics.apa_research import (  # noqa: E402
    CONFLUENCES, EXIT_VARIANTS, APAConfig, ablate, apply_combination, build_candidates, variant_key,
)
from backend.analytics.strategy_search import Bars  # noqa: E402
from backend.analytics.vwap_research import in_window, score, stats  # noqa: E402

MARKETS = ["GBPJPY", "EURJPY", "USDJPY", "GBPUSD", "EURUSD", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
           "AUDJPY", "CADJPY", "GBPAUD", "EURGBP", "GBPCHF", "EURAUD",
           "XAUUSD", "XAGUSD", "XPTUSD", "BTCUSD", "ETHUSD",
           "US Tech 100", "US SP 500", "Wall Street 30", "Germany 40", "UK 100", "Japan 225"]
T = lambda y, m, d: int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())
PICK = (T(2024, 3, 1), T(2026, 3, 12))
WINDOWS = {"2 years": (T(2024, 9, 12), T(2026, 9, 12)),
           "1 year": (T(2025, 9, 12), T(2026, 9, 12)),
           "6 months": (T(2026, 3, 12), T(2026, 9, 12))}
OUT = ROOT / "data" / "apa_research"

CORE: tuple[str, ...] = ()
SETS: list[tuple[str, tuple[str, ...]]] = [
    ("live default (no retest, no rejection)", ("head_not_breached", "session_ok")),
    ("bare core", ()),
    ("retest required", ("retest_occurred", "head_not_breached", "session_ok")),
    ("retest + rejection", ("retest_occurred", "zone_rejected", "head_not_breached", "session_ok")),
]
for f in CONFLUENCES:
    SETS.append((f"session+{f}", ("session_ok", f)))
SETS += [("session+trend+decisive", ("session_ok", "trend_align", "bos_decisive")),
         ("session+trend+near neckline", ("session_ok", "trend_align", "entry_near_neckline"))]


def fmt(s: dict) -> str:
    return f"{'-':>6}" if not s["n"] else f"{s['n']:>5} {s['avg_r']:+.3f}R {s['pf'] or 0:>5.2f} {s['t']:>+5.1f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="+", default=MARKETS)
    ap.add_argument("--out", default="apa_setups.pkl")
    args = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 not available: {mt5.last_error()}")

    saved, specs, pooled = {}, {}, []
    start = datetime(2024, 2, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 12, tzinfo=timezone.utc)
    for sym in args.markets:
        if not mt5.symbol_select(sym, True):
            print(f"\n{sym}: not on this account")
            continue
        info = mt5.symbol_info(sym)
        m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, start, end)
        m5 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, start, end)
        if m15 is None or m5 is None or len(m15) < 10_000 or len(m5) < 30_000:
            print(f"\n{sym}: not enough history")
            continue
        sb = Bars.from_rates(sym, "M15", m15, info.point)
        eb = Bars.from_rates(sym, "M5", m5, info.point)
        specs[sym] = {"value_per_price_per_lot": info.trade_tick_value / info.trade_tick_size if info.trade_tick_size else 0.0,
                      "min_lot": info.volume_min, "lot_step": info.volume_step, "max_lot": info.volume_max}
        cands = build_candidates(sb, eb, APAConfig())
        saved[sym] = cands
        pooled.extend(cands)
        print(f"\n==== {sym}: {len(cands)} patterns broke structure ====")
        if not cands:
            continue

        key = variant_key(3.0, True)
        print("  confluence            admits          blocks         edge added")
        for row in ablate(in_window(cands, *PICK), key):
            if not row["n_on"] and not row["n_blocked"]:
                continue
            print(f"  {row['confluence']:<20} {row['n_on']:>5} {row['avg_r_on'] or 0:+.3f}R   "
                  f"{row['n_blocked']:>5} {row['avg_r_blocked'] or 0:+.3f}R   "
                  f"{row['edge_added'] if row['edge_added'] is not None else 0:+.3f}R")

        # R:R question, on the live-default confluence set
        print("  target      " + "".join(f"{w:>26}" for w in WINDOWS))
        for t_, he in EXIT_VARIANTS:
            if not he:
                continue
            k = variant_key(t_, he)
            row = f"  {k:<12}"
            for wname, (lo, hi) in WINDOWS.items():
                sub = apply_combination(in_window(cands, lo, hi), use=("head_not_breached", "session_ok"))
                row += f"{fmt(stats(sub, k)):>26}"
            print(row)

        best = None
        for (name, use), (t_, he) in itertools.product(SETS, EXIT_VARIANTS):
            k = variant_key(t_, he)
            sc = score(apply_combination(in_window(cands, *PICK), use=use), k, 40)
            if best is None or sc > best[0]:
                best = (sc, name, use, k)
        if best and best[0] != float("-inf"):
            _, name, use, k = best
            print(f"  chosen in-sample: {name} | exit {k}")
            for wname, (lo, hi) in WINDOWS.items():
                print(f"    {wname:<10}{fmt(stats(apply_combination(in_window(cands, lo, hi), use=use), k))}")
            saved[f"{sym}__pick"] = (name, use, k)
    mt5.shutdown()

    if pooled:
        print(f"\n==== POOLED ({len(pooled)} patterns across {len(specs)} markets) ====")
        key = variant_key(3.0, True)
        print("  confluence            admits          blocks         edge added")
        for row in ablate(in_window(pooled, *PICK), key):
            if not row["n_on"] and not row["n_blocked"]:
                continue
            print(f"  {row['confluence']:<20} {row['n_on']:>5} {row['avg_r_on'] or 0:+.3f}R   "
                  f"{row['n_blocked']:>5} {row['avg_r_blocked'] or 0:+.3f}R   "
                  f"{row['edge_added'] if row['edge_added'] is not None else 0:+.3f}R")
        print("\n  pooled by target (live-default confluences):")
        for t_, he in EXIT_VARIANTS:
            if not he:
                continue
            k = variant_key(t_, he)
            for wname, (lo, hi) in WINDOWS.items():
                sub = apply_combination(in_window(pooled, lo, hi), use=("head_not_breached", "session_ok"))
                print(f"    {k:<12}{wname:<10}{fmt(stats(sub, k))}")

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / args.out, "wb") as f:
        pickle.dump({"candidates": saved, "specs": specs, "windows": {"pick": PICK, **WINDOWS}}, f)
    print(f"\nsaved {OUT / args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
