"""
scripts/verify_vwap_signals.py

Confirm VWAP produces signals at all, and that they carry chart markings.

Written after a gate-by-gate funnel showed `volume_confirmation_mult = 1.2`
eliminating every candidate on XAUUSD — 55 valid pullback setups reduced to
zero — which is why a 7.5-month gold backtest returned nothing and why the
chart had no VWAP confluences to draw.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Marking labels contain sigma/arrow glyphs; the Windows console defaults to
# cp1252 and raises UnicodeEncodeError on them. Replace rather than crash — the
# numbers matter more than the exact glyph.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd  # noqa: E402

import MetaTrader5 as mt5  # noqa: E402

from backend.core.config_schema import UserConfigV2  # noqa: E402
from backend.mt5.data_fetcher import DataFetcher  # noqa: E402
from backend.strategies.strategy_vwap import engine as E  # noqa: E402


async def main(symbol: str = "XAUUSD", count: int = 6000) -> int:
    mt5.initialize()
    df = await DataFetcher.get_historical_data(symbol, "M5", count=count)
    if df is None or df.empty:
        print("no data — is MT5 connected?")
        return 2
    df = df.set_index(pd.to_datetime(df["time"], unit="s")).sort_index()

    cfg = UserConfigV2()
    print(f"volume_confirmation_mult = {cfg.vwap.volume_confirmation_mult}")

    engine = E.VWAPEngine(cfg)
    signals = []
    t0 = time.perf_counter()
    for i in range(400, len(df)):
        sig = await engine.on_bar(symbol, "M5", df.iloc[max(0, i - 300):i])
        if sig:
            signals.append(sig)
    elapsed = time.perf_counter() - t0
    bars = len(df) - 400

    print(f"bars      : {bars}")
    print(f"signals   : {len(signals)}   (was 0 before the fix)")
    print(f"speed     : {elapsed / bars * 1000:.2f} ms/bar"
          f"  ->  {elapsed / bars * 17184 / 60:.1f} min for a 17k-bar run")

    if not signals:
        print("STILL ZERO — the volume gate was not the only blocker.")
        return 1

    print(f"setups    : {dict(Counter(s.signal_type for s in signals))}")
    first = signals[0]
    marks = (first.metadata or {}).get("markings") or []
    print(f"\nfirst signal: {first.direction} {first.signal_type} "
          f"@ {first.entry_price:.2f}  confluence={first.confluence_score}")
    print(f"markings on it: {len(marks)}")
    for m in marks[:8]:
        print(f"   {m['type']:<10} {m['label'][:34]:<36} role={m['role']}")

    # Every signal should carry geometry, or the chart will be blank for it.
    empty = sum(1 for s in signals if not ((s.metadata or {}).get("markings")))
    print(f"\nsignals with NO markings: {empty}/{len(signals)}"
          + ("  <-- chart would be blank for these" if empty else "  (all render)"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
