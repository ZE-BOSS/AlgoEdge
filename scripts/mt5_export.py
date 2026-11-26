"""
scripts/mt5_export.py - dump the broker's M5 history for the price-action study.

Finds each asset's name on this terminal (Deriv names differ from other brokers), fetches
M5 bars for the range and writes research_data/mt5/<asset>_m5.parquet.

    cd /opt/algoedge/demo/app && set -a && . ../instance.env && set +a && \
    PYTHONPATH=. ../venv/bin/python /opt/algoedge/research_data/mt5_export.py --out /opt/algoedge/research_data/mt5
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path.cwd()))

ASSETS = {  # asset -> names to try, first that exists wins
    "xauusd": ["XAUUSD"], "xagusd": ["XAGUSD"], "nas100": ["US Tech 100", "USTEC", "NAS100"],
    "us30": ["Wall Street 30", "US 30", "US30", "Wall Street"], "sp500": ["US SP 500", "US 500", "SPX500"],
    "ger40": ["Germany 40", "GER40", "DE40"], "uk100": ["UK 100", "UK100", "FTSE 100"],
    "jp225": ["Japan 225", "JP225"], "usoil": ["Oil/USD", "USOIL", "WTI", "US Oil"],
    "eurusd": ["EURUSD"], "gbpusd": ["GBPUSD"], "usdjpy": ["USDJPY"], "gbpjpy": ["GBPJPY"],
    "audusd": ["AUDUSD"], "usdcad": ["USDCAD"], "eurjpy": ["EURJPY"], "usdchf": ["USDCHF"],
}


async def main_async(a) -> None:
    import backend  # noqa: F401
    import MetaTrader5 as mt5
    from backend.mt5.data_fetcher import DataFetcher

    mt5.initialize()
    names = {s.name for s in (mt5.symbols_get() or [])}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    want = [x for x in a.assets.split(",") if x] or list(ASSETS)
    for asset in want:
        sym = next((n for n in ASSETS.get(asset, [asset]) if n in names), None)
        if sym is None:
            near = [n for n in names if asset[:3].upper() in n.upper().replace(" ", "")][:6]
            print(f"{asset:7s} not on this terminal (close: {near})", flush=True)
            continue
        try:
            df = await DataFetcher.get_data_range(sym, "M5", datetime.fromisoformat(a.start), datetime.fromisoformat(a.end))
            if "time" not in df.columns:
                df = df.reset_index().rename(columns={"index": "time"})
            df = df.sort_values("time").drop_duplicates("time")
            df["symbol"] = sym
            df.to_parquet(out / f"{asset}_m5.parquet")
            first = pd.to_datetime(df["time"].iloc[0], unit="s") if len(df) else None
            print(f"{asset:7s} {sym:16s} {len(df):7d} M5 bars from {first}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{asset:7s} {sym:16s} ERROR {type(e).__name__}: {e}", flush=True)
    print("EXPORT_DONE", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/opt/algoedge/research_data/mt5")
    ap.add_argument("--assets", default="")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-11-27")
    asyncio.run(main_async(ap.parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
