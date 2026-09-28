#!/usr/bin/env python
"""
scripts/fetch_fundednext_universe.py

Every symbol the FundedNext account can actually trade, not the eight I had been
using. The one published day-trading strategy with a strong Sharpe gets its edge
from SELECTING a handful of in-play names out of thousands; testing rules on a
fixed short list cannot reproduce that, and testing on 8 of 96 available symbols
was my own limitation, not the market's.

    python scripts/fetch_fundednext_universe.py --bars 99000
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "zone_study" / "fn_universe"
TERMINAL = r"C:\Program Files\MetaTrader 5\terminal64.exe"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=99000, help="M5 bars per symbol (maxbars is 100k)")
    ap.add_argument("--min-bars", type=int, default=20000)
    args = ap.parse_args()

    import MetaTrader5 as mt5

    if not mt5.initialize(path=TERMINAL):
        raise SystemExit(f"MT5 not available: {mt5.last_error()}")

    ai = mt5.account_info()
    print(f"account {ai.login} on {ai.server} | {ai.balance:,.2f} {ai.currency} "
          f"| leverage 1:{ai.leverage}")

    syms = mt5.symbols_get()
    groups = Counter(s.path.split("\\")[0] for s in syms)
    print(f"{len(syms)} symbols in {len(groups)} groups: {dict(groups)}")

    OUT.mkdir(parents=True, exist_ok=True)
    specs: dict[str, dict] = {}
    kept, skipped = 0, 0

    for s in syms:
        name = s.name
        if not mt5.symbol_select(name, True):
            skipped += 1
            continue
        r = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M5, 0, args.bars)
        if r is None or len(r) < args.min_bars:
            skipped += 1
            continue
        info = mt5.symbol_info(name)
        if not info or not info.trade_tick_size:
            skipped += 1
            continue
        np.savez_compressed(
            OUT / f"{name.replace(' ', '_').replace('/', '_')}.npz",
            time=r["time"], open=r["open"], high=r["high"], low=r["low"],
            close=r["close"], spread=r["spread"], tick_volume=r["tick_volume"],
            point=info.point,
        )
        specs[name] = {
            "point": info.point,
            "digits": info.digits,
            "group": s.path.split("\\")[0],
            "value_per_price_per_lot": info.trade_tick_value / info.trade_tick_size,
            "min_lot": info.volume_min,
            "lot_step": info.volume_step,
            "max_lot": info.volume_max,
            "median_spread_points": float(np.median(r["spread"])),
            "bars": int(len(r)),
            "first": datetime.fromtimestamp(int(r["time"][0]), timezone.utc).date().isoformat(),
        }
        kept += 1

    mt5.shutdown()
    (OUT / "specs.json").write_text(json.dumps(specs, indent=1))
    print(f"\ncached {kept} symbols, skipped {skipped}")
    by_group = Counter(v["group"] for v in specs.values())
    print("usable by group:", dict(by_group))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
