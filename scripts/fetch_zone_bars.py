#!/usr/bin/env python
"""
scripts/fetch_zone_bars.py

M5 bars for the round-number / zone study (2026-09-25), cached the same way
run_edge_lab.py caches its own: time (epoch s, UTC), OHLC, spread in POINTS,
tick_volume, and the symbol's point size.

    python scripts/fetch_zone_bars.py --markets "Step Index" "Crash 1000 Index" ...

Synthetics come from the Deriv terminal; the FX / index / crypto markets are
already cached under data/edge_lab/bars and are reused as-is.
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

OUT = ROOT / "data" / "zone_study"
BARS = OUT / "bars"

DEFAULT = [
    "Step Index",
    "Jump 25 Index", "Jump 75 Index", "Jump 100 Index",
    "Crash 900 Index", "Crash 1000 Index",
    "Boom 900 Index", "Boom 1000 Index",
    "Volatility 75 Index", "Volatility 100 Index",
]


def slug(sym: str) -> str:
    return sym.replace(" ", "_")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="*", default=DEFAULT)
    ap.add_argument("--start", default="2021-10-01")
    ap.add_argument("--min-bars", type=int, default=20000)
    ap.add_argument("--terminal", default=None,
                    help="which MT5 to read from: a path, or 'deriv' for DERIV_MT5_PATH. "
                         "Without this it attaches to whichever terminal was used last, "
                         "which is not the same thing as the one holding the symbols you "
                         "asked for -- a synthetic fetch that silently lands on the prop "
                         "terminal just reports every symbol as 'not on this account'.")
    args = ap.parse_args()

    import os

    import MetaTrader5 as mt5
    from dotenv import load_dotenv

    path = args.terminal
    if path == "deriv":
        load_dotenv(ROOT / ".env")
        path = os.getenv("DERIV_MT5_PATH") or None
    ok = mt5.initialize(path=path) if path else mt5.initialize()
    if not ok:
        raise SystemExit(f"MT5 not available: {mt5.last_error()} (path={path or 'auto'})")

    info_t = mt5.terminal_info()
    print(f"terminal: {info_t.company} | {info_t.path}")

    BARS.mkdir(parents=True, exist_ok=True)
    specs_path = OUT / "specs.json"
    specs = json.loads(specs_path.read_text()) if specs_path.exists() else {}

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)

    for sym in args.markets:
        if not mt5.symbol_select(sym, True):
            print(f"{sym}: not on this account")
            continue
        info = mt5.symbol_info(sym)
        # Sliced to the terminal's own limit: copy_rates_range measures a request
        # against the number of PERIODS the span covers, not the bars in it, so a
        # multi-year M5 range is refused outright even when far fewer bars exist.
        # Same fix as backend/mt5/data_fetcher.get_data_range.
        from backend.mt5.data_fetcher import terminal_max_bars
        span = terminal_max_bars() * 300
        cuts = list(range(int(start.timestamp()), int(end.timestamp()), span))
        cuts.append(int(end.timestamp()))
        pieces = []
        for lo_ts, hi_ts in zip(cuts, cuts[1:]):
            got = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, lo_ts, hi_ts)
            if got is not None and len(got):
                pieces.append(got)
        rates = np.concatenate(pieces) if pieces else None
        if rates is not None:
            rates = rates[np.argsort(rates["time"], kind="stable")]
            rates = rates[np.r_[True, np.diff(rates["time"]) > 0]]
        if rates is None or len(rates) < args.min_bars:
            got = 0 if rates is None else len(rates)
            print(f"{sym}: only {got} M5 bars (need {args.min_bars}) — skipped")
            continue
        np.savez_compressed(
            BARS / f"{slug(sym)}.npz",
            time=rates["time"], open=rates["open"], high=rates["high"],
            low=rates["low"], close=rates["close"], spread=rates["spread"],
            tick_volume=rates["tick_volume"], point=info.point,
        )
        specs[sym] = {
            "point": info.point,
            "digits": info.digits,
            "value_per_price_per_lot": (info.trade_tick_value / info.trade_tick_size
                                        if info.trade_tick_size else 0.0),
            "min_lot": info.volume_min,
            "lot_step": info.volume_step,
            "max_lot": info.volume_max,
        }
        first = datetime.fromtimestamp(int(rates["time"][0]), timezone.utc).date()
        last = datetime.fromtimestamp(int(rates["time"][-1]), timezone.utc).date()
        print(f"{sym}: {len(rates):,} M5 bars {first} -> {last} (point={info.point})")

    mt5.shutdown()
    specs_path.write_text(json.dumps(specs, indent=1))
    print(f"\nspecs -> {specs_path}")


if __name__ == "__main__":
    main()
