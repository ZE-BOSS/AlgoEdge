#!/usr/bin/env python
"""
scripts/harvest_study_bars.py

Fetch the bars for the 2026-09-14 confluence study through the app's own
DataFetcher (same server->UTC shift, same epoch conversion the backtester uses),
one market and timeframe at a time, and save each as a compact .npz.

    python scripts/harvest_study_bars.py --out <dir> [--markets ...] [--tfs M5 M15 H1 H4]

Each file holds time (int64 epoch s, bar open, UTC), open/high/low/close
(float64), spread (float64, PRICE units = points * symbol point) and
tick_volume (float64), plus the symbol's point and digits.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SYNTHETIC = ["Crash 1000 Index", "Crash 500 Index", "Crash 300 Index",
             "Boom 1000 Index", "Boom 500 Index", "Boom 300 Index",
             "Volatility 25 Index", "Volatility 75 Index", "Volatility 100 Index",
             "Jump 25 Index", "Jump 100 Index", "Step Index",
             "Range Break 100 Index", "Range Break 200 Index"]
REAL = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "GBPJPY", "XAUUSD", "XAGUSD",
        "BTCUSD", "ETHUSD", "US Tech 100", "US SP 500"]
MARKETS = SYNTHETIC + REAL


def fname(symbol: str, tf: str) -> str:
    return f"{symbol.replace(' ', '_')}_{tf}.npz"


async def harvest(symbol: str, tf: str, start: datetime, end: datetime, out: Path) -> str:
    import MetaTrader5 as mt5

    from backend.mt5.data_fetcher import DataFetcher

    path = out / fname(symbol, tf)
    if path.exists():
        return f"{symbol} {tf}: cached"
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    point = float(info.point) if info else 0.0
    digits = int(info.digits) if info else 0

    # Year-sized chunks: a single multi-year M5 request can exceed the terminal's
    # bar limit and silently return a truncated or empty frame.
    frames = []
    cur = pd.Timestamp(start)
    stop = pd.Timestamp(end)
    step = pd.Timedelta(days=120 if tf == "M5" else 365)
    while cur < stop:
        nxt = min(cur + step, stop)
        try:
            df = await DataFetcher.get_data_range(symbol, tf, cur.to_pydatetime(), nxt.to_pydatetime())
        except Exception:
            # No history that far back for this market (index CFDs start later):
            # skip the chunk, keep the rest.
            df = None
        if df is not None and not df.empty:
            frames.append(df)
        cur = nxt
    if not frames:
        return f"{symbol} {tf}: NO DATA"
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    vol_col = next((c for c in ("tick_volume", "volume", "real_volume") if c in df.columns), None)
    np.savez_compressed(
        path,
        time=df["time"].to_numpy(dtype=np.int64),
        open=df["open"].to_numpy(dtype=np.float64),
        high=df["high"].to_numpy(dtype=np.float64),
        low=df["low"].to_numpy(dtype=np.float64),
        close=df["close"].to_numpy(dtype=np.float64),
        spread=(df["spread"].to_numpy(dtype=np.float64) * point) if "spread" in df.columns
        else np.zeros(len(df)),
        tick_volume=df[vol_col].to_numpy(dtype=np.float64) if vol_col else np.zeros(len(df)),
        point=np.float64(point), digits=np.int64(digits),
    )
    first = datetime.utcfromtimestamp(int(df["time"].iloc[0])).strftime("%Y-%m-%d")
    last = datetime.utcfromtimestamp(int(df["time"].iloc[-1])).strftime("%Y-%m-%d")
    n = len(df)
    del df, frames
    gc.collect()
    return f"{symbol} {tf}: {n:,} bars {first} -> {last}"


async def main_async(args) -> None:
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit("MT5 not connected")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    for sym in args.markets:
        for tf in args.tfs:
            t0 = time.time()
            try:
                msg = await harvest(sym, tf, start, end, out)
            except Exception as e:  # keep going; report the gap
                msg = f"{sym} {tf}: FAILED {type(e).__name__}: {e}"
            print(f"{msg}  ({time.time() - t0:.0f}s)", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--markets", nargs="*", default=MARKETS)
    ap.add_argument("--tfs", nargs="*", default=["M5", "M15", "H1", "H4"])
    ap.add_argument("--start", default="2022-10-01")
    ap.add_argument("--end", default="2026-09-13")
    asyncio.run(main_async(ap.parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
