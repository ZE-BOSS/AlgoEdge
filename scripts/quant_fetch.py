"""Fetch as much history as the instance's MT5 terminal holds for the quant lab.

    rr demo /opt/algoedge/research/scripts/quant_fetch.py [out_dir]

Saves <out>/<symbol>_<tf>.csv.gz with UTC epoch times (DataFetcher normalises the
server offset) and prints the depth and median spread of each series.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, ".")
import pandas as pd  # noqa: E402

from backend.mt5.data_fetcher import DataFetcher  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/algoedge/research_out/qdata")
SYMBOLS = ["XAUUSD", "US Tech 100", "US SP 500", "Wall Street 30"]
TFS = ["M5", "M30", "H1"]


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        import MetaTrader5 as mt5
    except Exception:  # noqa: BLE001
        mt5 = None
    for sym in SYMBOLS:
        point = None
        if mt5 is not None:
            try:
                info = mt5.symbol_info(sym)
                point = float(info.point) if info else None
            except Exception:  # noqa: BLE001
                point = None
        for tf in TFS:
            try:
                df = await DataFetcher.get_historical_data(sym, tf, 99999)
            except Exception as e:  # noqa: BLE001
                print(f"{sym} {tf}: ERROR {e}"[:200], flush=True)
                continue
            keep = [c for c in ("time", "open", "high", "low", "close", "spread", "tick_volume") if c in df.columns]
            df = df[keep].copy()
            df.to_csv(OUT / f"{sym.replace(' ', '_')}_{tf}.csv.gz", index=False)
            t0 = pd.Timestamp(int(df.time.iloc[0]), unit="s")
            t1 = pd.Timestamp(int(df.time.iloc[-1]), unit="s")
            sp = float(df["spread"].median()) if "spread" in df else float("nan")
            print(f"{sym} {tf}: {len(df)} bars {t0:%Y-%m-%d} -> {t1:%Y-%m-%d} spread_pts {sp:.0f} point {point}",
                  flush=True)
    print("FETCH_DONE", flush=True)


asyncio.run(main())
